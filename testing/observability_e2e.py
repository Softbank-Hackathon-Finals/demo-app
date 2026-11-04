"""Owned local T22+T18+T36+T37 integration; not AWS/RKE2 migration."""

import argparse
import asyncio
import json
import os
import secrets
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets
from load_checks import whole_run_checks

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--loadgen", type=Path, required=True, help="T18 services/loadgen directory")
parser.add_argument(
    "--observability", type=Path, required=True, help="T37 services/observability directory"
)
parser.add_argument(
    "--windows", type=Path, required=True, help="T36 services/observability directory"
)
parser.add_argument(
    "--console",
    type=Path,
    help="Optional T21 services/console checkout for actual observation projection",
)
parser.add_argument("--console-browser", action="store_true", help="Also verify T21 in Chromium and WebKit")
parser.add_argument(
    "--output", type=Path, required=True, help="New private result directory; must not exist"
)
parser.add_argument(
    "--duration-per-environment",
    type=int,
    default=30,
    help="Seconds of 200 requests/s per environment; 30..1800, multiple of 10",
)
args = parser.parse_args()
if args.console_browser and not args.console:
    parser.error("--console-browser requires --console")
if not 30 <= args.duration_per_environment <= 1800 or args.duration_per_environment % 10:
    parser.error("duration must be 30..1800 seconds and a multiple of 10")
os.umask(0o077)
ROOT = args.output.resolve()
ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
OBS = args.observability.resolve()
T36 = args.windows.resolve()
LOAD = args.loadgen.resolve()
DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(T36 / "src"))
sys.path.insert(0, str(LOAD / "testing"))
# These imports deliberately follow explicit operator-selected checkout paths.
import e2e  # noqa: E402
from bravo_observability import judge  # noqa: E402
from bravo_observability.intake import SqliteWindowStore  # noqa: E402

# Use existing tool code and exact local image content; no T18 source modifications.
e2e.POSTGRES_IMAGE = "postgres:16"
e2e.EDGE_IMAGE = "nginx@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949"
project = "bravo-app-obs-" + uuid.uuid4().hex[:8]
token = secrets.token_hex(24)
window_token = secrets.token_hex(24)
cert = ROOT / "certs"
cert.mkdir()
env = dict(os.environ, CERT_DIR=str(cert), API_TOKEN=token)
for key in tuple(env):
    if key.startswith(("AWS_", "OPENAI_", "ANTHROPIC_", "GITHUB_", "GH_", "OTEL_", "BRAVO_")):
        env.pop(key, None)
env.update(
    CERT_DIR=str(cert),
    API_TOKEN=token,
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE=os.devnull,
    AWS_SHARED_CREDENTIALS_FILE=os.devnull,
)
os.environ.clear()
os.environ.update(env)
commands = ROOT / "commands.log"


def cmd(*args, timeout=120, data=None):
    p = subprocess.run(args, env=env, input=data, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        with commands.open("a") as f:
            f.write(p.stdout + p.stderr)
        raise RuntimeError(Path(args[0]).name + "_failed_" + str(p.returncode))
    return p.stdout.strip()


# Bind mounts and the host loopback are meaningful only for this machine.
# Refuse remote Docker contexts rather than creating trial resources there.
docker_endpoint = env.get("DOCKER_HOST")
if not docker_endpoint:
    docker_endpoint = json.loads(cmd("docker", "context", "inspect"))[0]["Endpoints"]["docker"][
        "Host"
    ]
if not docker_endpoint.startswith("unix://"):
    raise SystemExit("local_unix_docker_endpoint_required")


certscript = 'source ./certs.sh\nca bravo-ca\nleaf prometheus bravo-ca "DNS:prometheus,DNS:localhost,IP:127.0.0.1"\nleaf collector-onprem bravo-ca "DNS:collector-onprem"\nleaf collector-aws bravo-ca "DNS:collector-aws"\nleaf console-api bravo-ca "DNS:console-api"\nca app-ca\nleaf app-collector app-ca "DNS:app-collector,DNS:localhost,IP:127.0.0.1"\nleaf demo-app app-ca "DNS:demo-app"\nopen_for_containers\n'
subprocess.run(
    ["bash", "-c", certscript], env=env, cwd=OBS / "testing", check=True, stdout=subprocess.DEVNULL
)
override = {
    "services": {
        "collector-source": {
            "image": "otel/opentelemetry-collector-contrib:0.164.0@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b",
            "command": [
                "--config=/etc/otelcol/collector.yaml",
                "--feature-gates=-pkg.confighttp.PrioritizeNewKeepalive",
            ],
            "depends_on": {"queue-source-init": {"condition": "service_completed_successfully"}},
            "environment": {
                "BRAVO_ENV": "aws",
                "PLATFORM_OTLP_ENDPOINT": "https://prometheus:9090/api/v1/otlp",
            },
            "volumes": [
                str(OBS / "deploy/collector/collector.yaml") + ":/etc/otelcol/collector.yaml:ro",
                str(cert / "collector-aws") + ":/etc/bravo-tls:ro",
                "queue-source:/var/lib/otelcol/queue",
            ],
            "ports": ["127.0.0.1::4318"],
        },
        "queue-source-init": {
            "image": "busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec",
            "command": ["chown", "10001:10001", "/var/lib/otelcol/queue"],
            "volumes": ["queue-source:/var/lib/otelcol/queue"],
        },
    },
    "volumes": {"queue-source": {}},
}
(cert / "app-collector/client-ca.crt").write_bytes((cert / "app-ca.crt").read_bytes())
app_config = (
    str(OBS / "deploy/components/app-mtls/receiver-mtls.yaml")
    + ":/etc/otelcol-app/receiver-mtls.yaml:ro"
)
app_certs = str(cert / "app-collector") + ":/etc/bravo-app-tls:ro"
override["services"]["collector"] = {
    "command": [
        "--config=/etc/otelcol/collector.yaml",
        "--config=/etc/otelcol-app/receiver-mtls.yaml",
        "--feature-gates=-pkg.confighttp.PrioritizeNewKeepalive",
    ],
    "volumes": [app_config, app_certs],
}
override["services"]["collector-source"]["command"].append(
    "--config=/etc/otelcol-app/receiver-mtls.yaml"
)
override["services"]["collector-source"]["volumes"].extend([app_config, app_certs])
(ROOT / "override.json").write_text(json.dumps(override))
compose = [
    "docker",
    "compose",
    "-p",
    project,
    "-f",
    str(OBS / "testing/compose.yaml"),
    "-f",
    str(ROOT / "override.json"),
]


def dc(*args, **kwargs):
    return cmd(*compose, *args, **kwargs)


def port(service, p):
    return dc("port", service, str(p))


class Stack(e2e.Stack):
    def _app_env(self, name):
        e = super()._app_env(name)
        e["BRAVO_METRICS_CA_FILE"] = str(cert / "app-ca.crt")
        e["BRAVO_METRICS_CLIENT_CERT"] = str(cert / "demo-app/tls.crt")
        e["BRAVO_METRICS_CLIENT_KEY"] = str(cert / "demo-app/tls.key")
        e["BRAVO_METRICS_ENDPOINT"] = (
            "https://"
            + port("collector-source" if name == "aws" else "collector", 4318)
            + "/v1/metrics"
        )
        return e


class Harness(e2e.Harness):
    def _env(self):
        return super()._env() | {"BRAVO_WINDOW_TOKEN": window_token, "PYTHONPATH": str(T36 / "src")}


async def websocket_switch(stack):
    checks = {}
    name = "bravo-ws-edge-" + stack.tag
    edge_port = e2e.free_port()
    path = ROOT / "websocket-edge.conf"

    def config(app_port):
        path.write_text(
            'map $http_upgrade $connection_upgrade { default upgrade; "" close; }\nserver { listen 8080; access_log off; location / { proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_pass http://host.docker.internal:'
            + str(app_port)
            + "; proxy_read_timeout 60s; }}\n"
        )
        cmd("docker", "cp", str(path), name + ":/etc/nginx/conf.d/default.conf")

    cmd(
        "docker",
        "create",
        "--name",
        name,
        "--add-host=host.docker.internal:host-gateway",
        "-p",
        f"127.0.0.1:{edge_port}:8080",
        e2e.EDGE_IMAGE,
    )
    stack.containers.append(name)
    config(stack.source_port)
    cmd("docker", "start", name)
    edge = f"http://127.0.0.1:{edge_port}"
    e2e.wait_until(
        lambda: httpx.get(edge + "/healthz", trust_env=False).status_code == 200, "ws_edge"
    )
    headers = {"Authorization": "Bearer " + stack.admin_token}
    async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
        first = {
            "author": "integration",
            "body": "before-switch",
            "idempotency_key": str(uuid.uuid4()),
        }
        reply = await http.post(edge + "/api/messages", json=first)
        reply.raise_for_status()
        last = reply.json()["id"]
        checks["source_header_single"] = reply.headers.get_list("x-bravo-env") == ["aws"]
        async with websockets.connect(
            edge.replace("http:", "ws:") + "/ws?after=" + str(last), proxy=None
        ) as source:
            checks["source_hello"] = json.loads(await source.recv())["env"] == "aws"
            unauth = await http.post(stack.source_url + "/admin/websockets/close")
            checks["unauth_close_rejected"] = unauth.status_code == 401
            unfenced = await http.post(
                stack.source_url + "/admin/websockets/close", headers=headers
            )
            checks["unfenced_close_rejected"] = unfenced.status_code == 409
            blocked = await http.put(
                stack.source_url + "/admin/write-block", headers=headers, json={"blocked": True}
            )
            blocked.raise_for_status()
            pending = {
                "author": "integration",
                "body": "retry-after-switch",
                "idempotency_key": str(uuid.uuid4()),
            }
            denied = await http.post(edge + "/api/messages", json=pending)
            checks["write_blocked_retry_after"] = (
                denied.status_code == 503 and "retry-after" in denied.headers
            )
            config(stack.target_port)
            cmd("docker", "exec", name, "nginx", "-t")
            cmd("docker", "exec", name, "nginx", "-s", "reload")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (await http.get(edge + "/api/info", headers={"Connection": "close"})).json()[
                    "env"
                ] == "onprem":
                    break
                await asyncio.sleep(0.1)
            closed = await http.post(stack.source_url + "/admin/websockets/close", headers=headers)
            checks["exact_process_close_count"] = closed.status_code == 200 and closed.json() == {
                "closed": 1,
                "code": 1012,
            }
            try:
                async with asyncio.timeout(5):
                    while True:
                        await source.recv()
            except websockets.exceptions.ConnectionClosed as exc:
                checks["received_1012"] = exc.rcvd is not None and exc.rcvd.code == 1012
        # A fresh connection follows the same URL to the target, retaining cursor.
        async with websockets.connect(
            edge.replace("http:", "ws:") + "/ws?after=" + str(last), proxy=None
        ) as target:
            checks["target_hello"] = json.loads(await target.recv())["env"] == "onprem"
            sent = await http.post(
                edge + "/api/messages", json=pending, headers={"Connection": "close"}
            )
            sent.raise_for_status()
            again = await http.post(edge + "/api/messages", json=pending)
            again.raise_for_status()
            checks["target_header_single"] = sent.headers.get_list("x-bravo-env") == ["onprem"]
            checks["idempotent_retry_same_id"] = sent.json()["id"] == again.json()["id"]
            ids = []
            async with asyncio.timeout(5):
                while sent.json()["id"] not in ids:
                    event = json.loads(await target.recv())
                    if event["type"] == "messages":
                        ids.extend(m["id"] for m in event["items"])
            checks["cursor_no_old_or_duplicate"] = ids == [sent.json()["id"]]
            count = stack.admin_execute(
                "SELECT count(*) FROM messages WHERE idempotency_key=%s",
                (pending["idempotency_key"],),
            )[0][0]
            checks["exactly_one_database_row"] = count == 1
    return {
        "checks": checks,
        "scope": "actual local proxy/app switch; one shared PostgreSQL; not database cutover",
    }


result = {
    "scope": "actual local app/PostgreSQL/OTel/T36 HTTP; Nginx Edge substitute; shared DB; moto AWS APIs; not cloud migration",
    "status": "INCOMPLETE",
    "checks": {},
    "load_seconds_per_environment": args.duration_per_environment,
}
process = None
prom = None
try:
    print("output=" + str(ROOT), flush=True)
    import hashlib

    for name, directory in (("t18", LOAD), ("t36", T36), ("t37", OBS), ("demo", DEMO)):
        result[name + "_commit"] = cmd("git", "-C", str(directory), "rev-parse", "HEAD")
    if args.console:
        result["t21_commit"] = cmd("git", "-C", str(args.console.resolve()), "rev-parse", "HEAD")
    files = ["app/main.py", "app/environment.py", "app/metrics.py", "pyproject.toml", "uv.lock"]
    (ROOT / "app-source-hashes.json").write_text(
        json.dumps(
            {f: hashlib.sha256((DEMO / f).read_bytes()).hexdigest() for f in files}, indent=2
        )
    )

    dc("up", "-d", "--build", "prometheus", "collector", "collector-source", "api", timeout=240)
    api = "http://" + port("api", 8080)
    prometheus = "https://" + port("prometheus", 9090)
    tls = ssl.create_default_context(cafile=str(cert / "bravo-ca.crt"))
    tls.load_cert_chain(str(cert / "console-api/tls.crt"), str(cert / "console-api/tls.key"))
    prom = httpx.Client(verify=tls, trust_env=False, timeout=5)
    e2e.wait_until(lambda: prom.get(prometheus + "/-/ready").status_code == 200, "prometheus")
    intake_port = e2e.free_port()
    window_db = ROOT / "windows.db"
    ie = env | {
        "PYTHONPATH": str(T36 / "src"),
        "BRAVO_WINDOW_TOKEN": window_token,
        "BRAVO_WINDOW_PLANS": "plan-local",
        "BRAVO_WINDOW_DB": str(window_db),
    }
    log = (ROOT / "intake.log").open("w")
    process = subprocess.Popen(
        [
            str(OBS / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "bravo_observability.intake_api:from_env",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(intake_port),
            "--no-access-log",
        ],
        env=ie,
        stdout=log,
        stderr=log,
    )
    intake = f"http://127.0.0.1:{intake_port}"
    e2e.wait_until(
        lambda: httpx.get(intake + "/healthz", trust_env=False).status_code == 200, "intake"
    )
    with Stack(DEMO, ROOT) as stack:
        print("apps_ready", flush=True)
        collector_url = "https://" + port("collector", 4318) + "/v1/metrics"
        try:
            httpx.post(
                collector_url,
                content=b"",
                verify=ssl.create_default_context(cafile=str(cert / "app-ca.crt")),
                trust_env=False,
                timeout=3,
            )
            result["checks"]["collector_requires_client_certificate"] = False
        except httpx.TransportError:
            result["checks"]["collector_requires_client_certificate"] = True
        # Positive app exports below prove the Collector is reachable and healthy.

        h = Harness(stack, ROOT, args.duration_per_environment)
        for name, url in [("source", stack.source_url), ("target", stack.target_url)]:
            other_url = stack.target_url if name == "source" else stack.source_url

            def observe(process, name=name, other_url=other_url):
                # Keep both HTTP series active during longer trials. These
                # one-per-second info reads are display traffic, not T36 input.
                # The single T18 producer still owns the plan's window stream.
                start = time.monotonic()
                reads, failed = 0, 0
                with httpx.Client(trust_env=False, timeout=2) as client:
                    while process.poll() is None:
                        if time.monotonic() - start > args.duration_per_environment + 120:
                            process.terminate()
                            try:
                                process.wait(5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise RuntimeError("bounded_load_trial_timeout")
                        try:
                            failed += client.get(other_url + "/api/info").status_code != 200
                        except httpx.HTTPError:
                            failed += 1
                        reads += 1
                        if reads % 30 == 0:
                            (ROOT / "progress.json").write_text(
                                json.dumps(
                                    dict(
                                        phase=name,
                                        elapsed_seconds=round(time.monotonic() - start),
                                        display_reads=reads,
                                        display_failures=failed,
                                    )
                                )
                            )
                        time.sleep(1)
                result[name + "_display_traffic"] = dict(requests=reads, failures=failed)

            r = h.loadgen(
                name,
                url,
                duration=args.duration_per_environment,
                write=50,
                read=150,
                extra=("--window-url", intake + "/v1/observability/windows"),
                during=observe,
            )
            result["checks"][name + "_writes_reconciled"] = r.code == 0
            result["checks"][name + "_header_attribution"] = (
                sum(r.report["windows"]["unattributed_total"].values()) == 0
            )
            result["checks"].update(
                {
                    name + "_" + key: value
                    for key, value in whole_run_checks(
                        r.report, duration=args.duration_per_environment
                    ).items()
                }
            )
            result[name + "_report"] = r.report
            result[name + "_windows"] = r.windows
            print(name + "_load_complete code=" + str(r.code), flush=True)
        queryheaders = {"Authorization": "Bearer " + token}

        def view(path):
            r = httpx.get(api + path, headers=queryheaders, trust_env=False, timeout=5)
            r.raise_for_status()
            return r.json()

        result["observability"] = view("/v1/observability/environments")
        result["http"] = {
            name: view("/v1/observability/environments/" + name + "/http")
            for name in ("aws", "onprem")
        }

        def console_view(phase):
            console = args.console.resolve()
            config = {"origin": api, "token": token, "phase": phase}
            if args.console_browser:
                config["browser_output"] = str(ROOT / ("console-browser-" + phase))
            proc = subprocess.run(
                [str(console / ".venv/bin/python"), str(console / "testing/live_observations.py")],
                input=json.dumps(config),
                capture_output=True,
                text=True,
                env=env,
                cwd=console,
                timeout=100 if args.console_browser else 40,
            )
            if not proc.stdout or len(proc.stdout) > 65536:
                raise RuntimeError("console_qualification_failed")
            value = json.loads(proc.stdout)
            result["checks"]["console_" + phase] = (
                proc.returncode == 0 and value.get("status") == "PASS"
            )
            return value

        labels = prom.get(
            prometheus + "/api/v1/series",
            params={"match[]": '{__name__=~"http_server_request_duration.*"}'},
        ).json()
        result["checks"]["actual_http_histogram"] = bool(labels["data"])
        result["checks"]["no_metric_sensitive_labels"] = all(
            set(row)
            <= {
                "__name__",
                "bravo_env",
                "http_request_method",
                "http_response_status_code",
                "http_route",
                "instance",
                "job",
                "le",
                "otel_scope_name",
                "otel_scope_version",
                "otel_scope_schema_url",
            }
            for row in labels["data"]
        )
        result["checks"]["both_environments"] = set(r.get("bravo_env") for r in labels["data"]) == {
            "aws",
            "onprem",
        }
        result["series_count"] = len(labels["data"])
        windows = SqliteWindowStore(str(window_db))
        latest = windows.latest("plan-local")
        result["checks"]["window_http_received"] = latest is not None
        result["latest_window"] = latest
        profile = json.loads(
            (T36.parent.parent / "docs/observability/threshold-profiles.json").read_text()
        )["profiles"][0]
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        result["latest_judgement"] = judge(
            profile,
            latest,
            plan_id="plan-local",
            state="SHIFT",
            weight_target=100,
            observe_from=latest["window"]["start"],
            decided_at=now.isoformat(),
            prior_unknown=0,
        )
        result["checks"]["bad_intake_token_rejected"] = (
            httpx.post(
                intake + "/v1/observability/windows", json=latest, trust_env=False
            ).status_code
            == 401
        )
        result["checks"]["replayed_window_rejected"] = (
            httpx.post(
                intake + "/v1/observability/windows",
                json=latest,
                headers={"Authorization": "Bearer " + window_token},
                trust_env=False,
            ).status_code
            == 409
        )
        # Keep source app producing while destination Collector is stopped.
        # This is only the last display window. Whole-run checks above are
        # separate so an earlier outage cannot be hidden by later recovery.
        result["checks"]["final_display_window_zero_errors"] = all(
            v["error_ratio"] == 0 for v in result["http"].values()
        )
        # Qualify the bounded T36 window before optional browser startup adds
        # time. Never refresh the original window timestamp for a later test.
        if args.console:
            result["console_collecting"] = console_view("collecting")
        result["websocket_switch"] = asyncio.run(websocket_switch(stack))
        result["checks"]["websocket_switch_verified"] = all(
            result["websocket_switch"]["checks"].values()
        )
        dc("stop", "collector")
        for _ in range(40):
            httpx.get(stack.source_url + "/api/info", trust_env=False, timeout=3)
            time.sleep(1)
        result["after_collector_stop"] = {
            name: view("/v1/observability/environments/" + name + "/http")
            for name in ("aws", "onprem")
        }
        result["checks"]["collector_stop_not_zero"] = result["after_collector_stop"]["onprem"][
            "state"
        ] == "UNOBSERVABLE" and all(
            result["after_collector_stop"]["onprem"][k] is None
            for k in ("error_ratio", "p95_seconds", "requests_per_second")
        )
        result["checks"]["source_still_observed"] = (
            result["after_collector_stop"]["aws"]["state"] == "OK"
        )
        if args.console:
            result["console_after_collector_stop"] = console_view("target-stopped")
        print("collector_stop_observed", flush=True)
    result["status"] = "PASS" if all(result["checks"].values()) else "FAIL"
except Exception as ex:
    result["failure_type"] = type(ex).__name__
    import traceback

    (ROOT / "private-error.log").write_text(traceback.format_exc())
    print("integration_failed=" + type(ex).__name__, flush=True)
finally:
    if prom:
        prom.close()
    if process:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    try:
        dc("down", "-v", "--remove-orphans")
        result["owned_compose_removed"] = True
    except Exception:
        result["owned_compose_removed"] = False
    (ROOT / "result.json").write_text(json.dumps(result, indent=2, default=str))
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": result["checks"],
                "output": str(ROOT),
                "failure": result.get("failure_type"),
            }
        ),
        flush=True,
    )

if result["status"] != "PASS" or not result.get("owned_compose_removed"):
    raise SystemExit(1)

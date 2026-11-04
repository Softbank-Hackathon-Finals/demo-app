"""Owned SeaweedFS qualification; no team/cloud resources or real credentials."""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.umask(0o077)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--platform",
    type=Path,
    required=True,
    help="Reviewed platform checkout containing T8 container helper",
)
parser.add_argument(
    "--postgres-bin",
    type=Path,
    required=True,
    help="Native PostgreSQL binaries; creates a new Unix-socket instance",
)
parser.add_argument(
    "--output", type=Path, required=True, help="New private directory; must not already exist"
)
args = parser.parse_args()
OUT = args.output.resolve()
OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
APP = Path(__file__).resolve().parents[1]
HELPER = args.platform.resolve() / "scripts/tests"
sys.path[:0] = [str(APP), str(HELPER)]
import attachment_containers as lab  # noqa: E402
from botocore.exceptions import EndpointConnectionError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import AttachmentConflict, AttachmentStorage, AttachmentUnavailable  # noqa: E402
from tests.test_api import FakeStore  # noqa: E402

# Never point at a remote Docker daemon, even if the operator's shell has one set.
if os.environ.get("DOCKER_HOST"):
    endpoint = os.environ["DOCKER_HOST"]
else:
    endpoint = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0][
        "Endpoints"
    ]["docker"]["Host"]
if not endpoint.startswith("unix://"):
    raise SystemExit("local_docker_required")
name = "t22-immutable-" + secrets.token_hex(6)
cid = None
pg_root = None
pg_started = False
pg_bin = args.postgres_bin.resolve()
base_env = {
    k: v
    for k, v in os.environ.items()
    if not k.startswith(("AWS_", "GH_", "GITHUB_", "OPENAI_", "ANTHROPIC_"))
}
base_env["AWS_EC2_METADATA_DISABLED"] = "true"


def owned_docker(*args, **kwargs):
    global cid
    command = ["docker"]
    values = dict(base_env)
    args = list(args)
    if args[0] == "run":
        args[1:1] = [
            "--memory",
            "512m",
            "--cpus",
            "0.75",
            "--label",
            "bravo.validation=t22-immutable",
        ]
        for index, value in enumerate(args):
            if index and args[index - 1] == "-e" and "=" in value:
                key, content = value.split("=", 1)
                values[key] = content
                args[index] = key
    proc = subprocess.run(command + args, env=values, capture_output=True, timeout=200, **kwargs)
    if proc.returncode:
        raise RuntimeError("owned_docker_command_failed")
    if args[0] == "run":
        cid = proc.stdout.decode().strip()
    return proc


lab.docker = owned_docker
result = {
    "status": "INCOMPLETE",
    "scope": "local SeaweedFS conditional writes, fixture then real PostgreSQL metadata; loopback HTTP; no AWS/RKE2 qualification",
    "checks": {},
    "owned_container_removed": False,
}
try:
    environment = lab.start_target(name)
    with patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": environment["target_client"][0],
            "AWS_SECRET_ACCESS_KEY": environment["target_client"][1],
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        },
        clear=True,
    ):
        storage = AttachmentStorage(lab.BUCKET, environment["target_endpoint"], True)
    client = storage.client
    result["checks"]["actual_application_sdk_configuration"] = True
    result["image"] = environment["target_image"]
    key = "attachments/" + str(uuid.uuid4())
    storage.put(key, b"original", "text/plain")
    storage.put(key, b"original", "text/plain")
    result["checks"]["identical_retry_verified"] = True
    try:
        storage.put(key, b"replaced", "text/plain")
    except AttachmentConflict:
        result["checks"]["same_size_different_content_rejected"] = True
    else:
        raise AssertionError("content_overwritten")
    assert storage.get(key) == b"original"
    try:
        storage.put(key, b"original", "image/png")
    except AttachmentConflict:
        result["checks"]["different_type_rejected"] = True
    else:
        raise AssertionError("content_type_changed")
    # Two or more competing requests must never both be acknowledged with different content.
    race = "attachments/" + str(uuid.uuid4())

    def attempt(i):
        data = f"body-{i:02d}".encode()
        try:
            storage.put(race, data, "text/plain")
            return "accepted", data
        except AttachmentConflict:
            return "conflict", data
        except AttachmentUnavailable:
            return "unknown", data

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    accepted = [body for state, body in outcomes if state == "accepted"]
    assert len(accepted) == 1 and storage.get(race) == accepted[0]
    result["checks"]["concurrent_distinct_payloads_single_winner"] = True
    result["concurrent_counts"] = {
        state: sum(s == state for s, _ in outcomes) for state in ("accepted", "conflict", "unknown")
    }
    # Drop only the acknowledgement after the real conditional write, then retry the same key.
    lost_key = "attachments/" + str(uuid.uuid4())
    real_put = client.put_object

    def lose(**kwargs):
        real_put(**kwargs)
        raise EndpointConnectionError(endpoint_url="http://loopback-test.invalid")

    client.put_object = lose
    try:
        try:
            storage.put(lost_key, b"committed", "text/plain")
        except AttachmentUnavailable:
            pass
        else:
            raise AssertionError("lost_response_reported_success")
    finally:
        client.put_object = real_put
    storage.put(lost_key, b"committed", "text/plain")
    assert storage.get(lost_key) == b"committed"
    result["checks"]["lost_ack_retry_reads_same_committed_object"] = True
    # API mapping with the actual object store; DB behavior remains a named test double.
    settings = Settings("local", "unused", 5432, "unused", "unused", "unused", None, False, None)
    ident = str(uuid.uuid4())
    store = FakeStore()
    with TestClient(create_app(settings, store, storage)) as http:
        url = "/api/attachments/" + ident
        assert (
            http.put(url, files={"file": ("same.txt", b"original", "text/plain")}).status_code
            == 200
        )
        assert (
            http.put(url, files={"file": ("same.txt", b"replaced", "text/plain")}).status_code
            == 409
        )
        assert (
            http.put(url, files={"file": ("same.txt", b"original", "text/plain")}).status_code
            == 200
        )
        assert http.get(url).content == b"original"
    result["checks"]["real_store_http_conflict_and_idempotent_retry"] = True
    # Combine the real object store with a new native PostgreSQL instance and the
    # real app/Store. Force an actual metadata INSERT denial after object creation.
    import httpx
    import psycopg
    from psycopg_pool import AsyncConnectionPool

    from app.store import Store

    # A long evidence directory can exceed macOS's Unix socket path limit.
    # Allocate a new short directory owned by this trial instead.
    pg_root = Path(tempfile.mkdtemp(prefix="t22-object-", dir="/tmp"))
    pg_env = dict(base_env, PATH=str(pg_bin) + ":" + base_env["PATH"])

    def pg_call(*args):
        subprocess.run([str(x) for x in args], env=pg_env, check=True, capture_output=True)

    pg_call(
        pg_bin / "initdb",
        "-D",
        pg_root / "data",
        "--auth-local=trust",
        "--auth-host=reject",
        "--no-locale",
        "--encoding=UTF8",
    )
    pg_call(
        pg_bin / "pg_ctl",
        "-D",
        pg_root / "data",
        "-l",
        pg_root / "postgres.log",
        "-o",
        f"-k {pg_root} -h ''",
        "-w",
        "start",
    )
    pg_started = True
    admin_dsn = f"host={pg_root} dbname=postgres"
    with psycopg.connect(admin_dsn) as connection:
        result["postgres_version_num"] = connection.info.server_version
    pg_call(
        pg_bin / "psql",
        admin_dsn,
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "CREATE ROLE chat_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION",
    )
    pg_call(pg_bin / "psql", admin_dsn, "-v", "ON_ERROR_STOP=1", "-f", APP / "db/schema.sql")

    async def metadata_trial():
        async with AsyncConnectionPool(
            admin_dsn + " user=chat_app", min_size=1, max_size=2, kwargs={"prepare_threshold": None}
        ) as pool:
            app = create_app(settings, Store(pool, 200), storage)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://local-test.invalid",
            ) as http:
                ident = str(uuid.uuid4())
                url = "/api/attachments/" + ident

                def privilege(sql):
                    with psycopg.connect(admin_dsn, autocommit=True) as c:
                        c.execute(sql)

                def count():
                    with psycopg.connect(admin_dsn) as c:
                        return c.execute(
                            "SELECT count(*) FROM attachments WHERE id=%s", (ident,)
                        ).fetchone()[0]

                privilege("REVOKE INSERT ON attachments FROM chat_app")
                denied = await http.put(
                    url, files={"file": ("same.txt", b"metadata-retry", "text/plain")}
                )
                assert denied.status_code == 500 and count() == 0
                assert storage.get("attachments/" + ident) == b"metadata-retry"
                privilege("GRANT INSERT ON attachments TO chat_app")
                recovered = await http.put(
                    url, files={"file": ("same.txt", b"metadata-retry", "text/plain")}
                )
                assert recovered.status_code == 200 and count() == 1
                assert (
                    await http.put(
                        url, files={"file": ("same.txt", b"metadata-retry", "text/plain")}
                    )
                ).status_code == 200 and count() == 1
                assert (
                    await http.put(
                        url, files={"file": ("same.txt", b"metadata-other", "text/plain")}
                    )
                ).status_code == 409
                assert (await http.get(url)).content == b"metadata-retry"
                result["checks"]["real_postgres_denied_metadata_no_row"] = True
                result["checks"]["real_postgres_same_key_recovery_one_row"] = True
                result["checks"]["real_postgres_object_conflict_preserves_original"] = True

    asyncio.run(metadata_trial())
    result["metadata_scope"] = (
        "native PostgreSQL Unix socket, non-admin role, actual INSERT revoke/grant; real ASGI API and SeaweedFS; no distributed transaction"
    )
    result["source_sha256"] = {
        p: hashlib.sha256((APP / p).read_bytes()).hexdigest()
        for p in ("app/main.py", "app/storage.py")
    }
    result["status"] = "PASS"
finally:
    if pg_started:
        stopped = subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(pg_root / "data"), "-m", "fast", "-w", "stop"],
            capture_output=True,
            timeout=30,
        )
        result["owned_database_stopped"] = stopped.returncode == 0
    if cid:
        # Exact ID returned by this invocation; never name/prefix-delete existing resources.
        proc = subprocess.run(
            ["docker", "rm", "-f", cid], env=base_env, capture_output=True, timeout=30
        )
        result["owned_container_removed"] = proc.returncode == 0
    (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))

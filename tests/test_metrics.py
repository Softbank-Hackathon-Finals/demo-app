"""Real SDK/protobuf and loopback HTTP export; app DB remains a fixture here."""

import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.main import create_app
from app.metrics import HttpMetrics, validate_endpoint
from tests.test_api import ADMIN, FakeStorage, FakeStore, message
from tests.test_resilience import client_with


def app_with_metrics(metrics, store=None, **kwargs):
    app = create_app(
        client_with(FakeStore()).app.state.settings, store or FakeStore(), FakeStorage()
    )
    client = TestClient(app, **kwargs)
    client.__enter__()
    app.state.http_metrics = metrics
    return client


def data_points(reader):
    data = reader.get_metrics_data()
    return [
        p
        for r in data.resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
        for p in m.data.data_points
    ]


def test_routes_status_and_sensitive_values_are_allowlisted(monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "secret=PRIVATE_RESOURCE")
    reader = InMemoryMetricReader()
    metrics = HttpMetrics([reader])
    client = app_with_metrics(metrics)
    try:
        client.get(
            "/api/info?token=PRIVATE_QUERY", headers={"Authorization": "Bearer PRIVATE_TOKEN"}
        )
        client.post(
            "/api/messages", json=message() | {"body": "PRIVATE_BODY", "author": "PRIVATE_NAME"}
        )
        client.get("/api/attachments/" + str(uuid.uuid4()))
        client.get("/not-found-PRIVATE_PATH")
        client.put("/admin/write-block", headers=ADMIN, json={"blocked": True})
        client.post("/api/messages", json=message())
        serialized = reader.get_metrics_data().to_json()
        assert "PRIVATE_" not in serialized
        points = data_points(reader)
        assert sum(p.count for p in points) == 6
        assert any(p.attributes["http.response.status_code"] == 503 for p in points)
        assert any(p.attributes["http.route"] == "/api/attachments/{attachment_id}" for p in points)
        assert all(
            set(p.attributes) == {"http.route", "http.request.method", "http.response.status_code"}
            for p in points
        )
        assert all(not p.exemplars for p in points)
        assert all(p.sum >= 0 for p in points)
    finally:
        client.__exit__(None, None, None)
        metrics.close()


def test_unhandled_failure_is_measured_as_500():
    class Broken(FakeStore):
        async def list_messages(self, *args):
            raise RuntimeError("PRIVATE_EXCEPTION")

    reader = InMemoryMetricReader()
    metrics = HttpMetrics([reader])
    client = app_with_metrics(metrics, Broken(), raise_server_exceptions=False)
    try:
        assert client.get("/api/messages").status_code == 500
        assert data_points(reader)[0].attributes["http.response.status_code"] == 500
        assert "PRIVATE_EXCEPTION" not in reader.get_metrics_data().to_json()
    finally:
        client.__exit__(None, None, None)
        metrics.close()


def test_metrics_failure_does_not_change_response():
    class Broken:
        def record(self, *args):
            raise RuntimeError("PRIVATE_DIAGNOSTIC")

    client = app_with_metrics(Broken())
    try:
        assert client.get("/healthz").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_metrics_worker_closes_when_database_startup_fails(monkeypatch):
    closed = []

    class Worker:
        def close(self):
            closed.append(True)

    def denied(*_args):
        raise RuntimeError("db_secret_unavailable")

    monkeypatch.setattr(HttpMetrics, "from_env", lambda: Worker())
    monkeypatch.setattr("app.main.fetch_db_credentials", denied)
    settings = client_with(FakeStore()).app.state.settings
    app = create_app(settings, storage=FakeStorage())
    with pytest.raises(RuntimeError, match="db_secret_unavailable"), TestClient(app):
        pass
    assert closed == [True]


def test_http_histogram_excludes_websocket_session():
    reader = InMemoryMetricReader()
    metrics = HttpMetrics([reader])
    client = app_with_metrics(metrics)
    try:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
        assert reader.get_metrics_data() is None
    finally:
        client.__exit__(None, None, None)
        metrics.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://collector.example/v1/metrics",
        "https://u:p@collector/v1/metrics",
        "https://collector/v1/metrics?token=secret",
        "https://collector/v1/metrics#fragment",
        "https://collector/other",
        " https://collector/v1/metrics",
        "https://collector:0/v1/metrics",
        "https://collector:99999/v1/metrics",
        "file:///v1/metrics",
        "https://collector\\x/v1/metrics",
    ],
)
def test_invalid_metrics_endpoint_rejected(endpoint):
    with pytest.raises(ValueError, match="invalid_fixed_metrics_endpoint"):
        validate_endpoint(endpoint)


def test_disabled_by_default_and_ambient_credentials_are_not_inherited(monkeypatch):
    monkeypatch.delenv("BRAVO_METRICS_ENDPOINT", raising=False)
    assert HttpMetrics.from_env() is None
    monkeypatch.setenv("BRAVO_METRICS_ENDPOINT", "https://collector.example/v1/metrics")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=PRIVATE_CREDENTIAL")
    with pytest.raises(ValueError, match="ambient_otlp"):
        HttpMetrics.from_env()


def test_actual_otlp_protobuf_export_no_redirect_or_proxy(monkeypatch):
    calls = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, dict(self.headers), raw))
            self.send_response(307 if len(calls) == 1 else 200)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "BRAVO_METRICS_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/metrics"
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    metrics = HttpMetrics.from_env()
    try:
        metrics.record({"method": "GET", "route": SimpleNamespace(path="/api/info")}, 200, 0.125)
        metrics.provider.force_flush(timeout_millis=3000)
        assert len(calls) == 1 and calls[0][0] == "/v1/metrics"
        parsed = ExportMetricsServiceRequest.FromString(calls[0][2])
        metric = parsed.resource_metrics[0].scope_metrics[0].metrics[0]
        assert metric.name == "http.server.request.duration" and metric.unit == "s"
        assert metric.histogram.data_points[0].count == 1
        assert metric.histogram.data_points[0].sum == 0.125
        assert all("authorization" != k.lower() for k in calls[0][1])
        assert "/must-not-follow" not in [c[0] for c in calls]
    finally:
        metrics.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def metrics_tls(tmp_path):
    import shutil
    import subprocess

    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL required for actual TLS verification")

    def run(*args):
        subprocess.run([openssl, *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "ca.cnf").write_text(
        "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ca\n"
        "[dn]\nCN=metrics-test-ca\n[ca]\nbasicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid:always\n"
    )
    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=metrics-test-ca",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-config",
        "ca.cnf",
    )
    for name, usage in (("server", "serverAuth"), ("client", "clientAuth")):
        run(
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
        )
        (tmp_path / f"{name}.ext").write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
            f"extendedKeyUsage={usage}\nsubjectAltName=IP:127.0.0.1\n"
        )
        run(
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-out",
            f"{name}.crt",
            "-extfile",
            f"{name}.ext",
        )
    return tmp_path


def test_actual_mtls_requires_trusted_server_and_client(metrics_tls):
    import ssl

    import requests

    from app.metrics import FixedSession

    accepted = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            accepted.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(metrics_tls / "server.crt", metrics_tls / "server.key")
    context.load_verify_locations(metrics_tls / "ca.crt")
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://127.0.0.1:{server.server_port}/v1/metrics"
    pair = (str(metrics_tls / "client.crt"), str(metrics_tls / "client.key"))
    try:
        with FixedSession() as session:
            with pytest.raises(requests.exceptions.SSLError):
                session.post(url, data=b"not-delivered", cert=pair, timeout=2)
            with pytest.raises(requests.exceptions.SSLError):
                session.post(
                    url, data=b"not-delivered", verify=str(metrics_tls / "ca.crt"), timeout=2
                )
            response = session.post(
                url, data=b"verified", cert=pair, verify=str(metrics_tls / "ca.crt"), timeout=2
            )
            assert response.status_code == 200
            assert accepted == [b"verified"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_collector_error_redacted_and_response_size_bounded():
    import requests

    from app.metrics import FixedSession

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(400, "PRIVATE_REASON")
            self.end_headers()
            try:
                self.wfile.write(b"x" * 70000 if self.path == "/large" else b"PRIVATE_BODY")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with FixedSession() as session:
            response = session.post(
                f"http://127.0.0.1:{server.server_port}/small", data=b"x", timeout=2
            )
            assert response.status_code == 400
            assert response.content == b"" and "PRIVATE" not in response.reason
            with pytest.raises(requests.RequestException, match="metrics_response_too_large"):
                session.post(f"http://127.0.0.1:{server.server_port}/large", data=b"x", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

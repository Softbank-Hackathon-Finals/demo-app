from dataclasses import replace

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient
from psycopg import OperationalError

from app.main import create_app
from tests.test_api import ADMIN, FakeStorage, FakeStore, message
from tests.test_resilience import DownStore, client_with


@pytest.fixture
def client():
    prototype = client_with(FakeStore())
    settings = replace(prototype.app.state.settings, app_env="aws")
    with TestClient(create_app(settings, FakeStore(), FakeStorage())) as c:
        yield c


def assert_environment(response, status, environment="aws"):
    assert response.status_code == status
    assert response.headers.get_list("X-Bravo-Env") == [environment]


def test_success_errors_and_static_responses_have_environment(client):
    for path in ("/healthz", "/readyz", "/api/info", "/api/messages", "/"):
        assert_environment(client.get(path, headers={"X-Bravo-Env": "forged"}), 200)
    payload = message()
    assert_environment(client.post("/api/messages", json=payload), 201)
    assert_environment(client.post("/api/messages", json=payload), 200)
    assert_environment(client.post("/api/messages", json=payload | {"body": "other"}), 409)
    assert_environment(client.post("/api/messages", json={}), 422)
    assert_environment(client.get("/missing"), 404)
    assert_environment(client.get("/admin/write-block"), 401)
    client.put("/admin/write-block", json={"blocked": True}, headers=ADMIN)
    blocked = client.post("/api/messages", json=message())
    assert_environment(blocked, 503)
    assert blocked.headers["Retry-After"] == "2"


def test_database_failure_keeps_environment_and_retry_contract():
    with client_with(DownStore(OperationalError("unavailable"))) as c:
        for response in (c.get("/api/messages"), c.post("/api/messages", json=message())):
            assert_environment(response, 503, "onprem")
            assert response.headers["Retry-After"] == "2"


def test_unhandled_500_is_attributed_without_exposing_error(client):
    class BrokenStore(FakeStore):
        async def list_messages(self, *args):
            raise RuntimeError("private diagnostic")

    app = create_app(client.app.state.settings, BrokenStore(), FakeStorage())
    with TestClient(app, raise_server_exceptions=False) as c:
        result = c.get("/api/messages")
        assert_environment(result, 500)
        assert "private diagnostic" not in result.text


def test_response_header_cannot_override_server_label(client):
    @client.app.get("/test-header")
    def test_header():
        response = Response("ok")
        response.raw_headers.extend([
            (b"X-Bravo-Env", b"forged"), (b"x-bravo-env", b"second"),
            (b"set-cookie", b"a=1"), (b"set-cookie", b"b=2"),
        ])
        return response

    # The static mount normally consumes later routes. Put this test route first.
    client.app.router.routes.insert(0, client.app.router.routes.pop())
    result = client.get("/test-header")
    assert_environment(result, 200)
    assert result.headers.get_list("set-cookie") == ["a=1", "b=2"]


@pytest.mark.parametrize("label", ["", "a\r\nInjected: yes", "a\x00b", "환경", "a" * 129, " a"])
def test_invalid_environment_fails_before_serving(client, label):
    with pytest.raises(ValueError, match="APP_ENV"):
        create_app(replace(client.app.state.settings, app_env=label), FakeStore(), FakeStorage())


def test_websocket_contract_unchanged(client):
    with client.websocket_connect("/ws", headers={"X-Bravo-Env": "forged"}) as ws:
        assert ws.receive_json() == {"type": "hello", "env": "aws", "write_blocked": False}

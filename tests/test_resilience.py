import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError, errors
from psycopg_pool import PoolTimeout

from app.config import Settings
from app.db_secret import DbCredentials
from app.main import create_app
from tests.test_api import FakeStorage, FakeStore


class DownStore(FakeStore):
    """DB가 멈춘 상황(컷오버 중 PAUSE, 연결 끊김, 풀 대기 초과, statement_timeout)."""

    def __init__(self, exc: Exception):
        super().__init__()
        self.exc = exc

    async def ping(self):
        raise self.exc

    async def add_message(self, *args):
        raise self.exc

    async def list_messages(self, *args):
        raise self.exc


def client_with(store):
    settings = Settings(
        app_env="onprem",
        db_host="unused",
        db_port=5432,
        db_name="unused",
        db_secret_id="unused",
        s3_bucket="unused",
        s3_endpoint_url=None,
        s3_force_path_style=False,
        admin_token="test-admin",
        poll_interval=0.05,
    )
    return TestClient(create_app(settings, store, FakeStorage()))


@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("connection lost"),
        PoolTimeout("pool exhausted"),
        errors.QueryCanceled("timeout"),
    ],
    ids=["connection", "pool-timeout", "statement-timeout"],
)
def test_database_failure_is_503_with_retry_after(exc):
    with client_with(DownStore(exc)) as c:
        message = {"idempotency_key": str(uuid.uuid4()), "author": "a", "body": "b"}
        for response in (c.post("/api/messages", json=message), c.get("/api/messages")):
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "2"


def test_readiness_does_not_depend_on_database():
    with client_with(DownStore(OperationalError("paused"))) as c:
        assert c.get("/readyz").status_code == 200
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/health/db").status_code == 503


def test_websocket_survives_database_outage():
    with client_with(DownStore(OperationalError("paused"))) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            c.put(
                "/admin/write-block",
                json={"blocked": True},
                headers={"Authorization": "Bearer test-admin"},
            )
            # DB가 멈춰 있어도 연결은 유지되고 상태 변경은 전달된다.
            assert ws.receive_json() == {"type": "status", "env": "onprem", "write_blocked": True}


def test_credentials_are_cached_and_refreshed():
    calls = []

    def fetch():
        calls.append(1)
        return "chat_app", f"pw-{len(calls)}"

    async def scenario():
        creds = DbCredentials(fetch, ttl_seconds=300)
        assert await creds.get() == ("chat_app", "pw-1")
        assert await creds.get() == ("chat_app", "pw-1")  # 캐시
        creds.invalidate()  # 인증 실패 → 다시 읽음
        assert await creds.get() == ("chat_app", "pw-2")
        expired = DbCredentials(fetch, ttl_seconds=0)
        await expired.get()
        await asyncio.sleep(0.01)
        assert await expired.get() == ("chat_app", "pw-4")  # TTL 지나면 다시 읽음

    asyncio.run(scenario())
    assert len(calls) == 4

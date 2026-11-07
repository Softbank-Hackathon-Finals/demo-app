"""T46 메시지 개인정보 검사 요청: 저장 뒤 큐 송신, 실패 시 503, 같은 멱등 키 재요청 때 다시 송신."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.events import EventUnavailable
from app.main import create_app
from tests.test_api import FakeStorage, FakeStore, message


class FakeEvents:
    def __init__(self):
        self.sent: list[int] = []
        self.fail = False

    def message_created(self, message_id):
        if self.fail:
            raise EventUnavailable("EndpointConnectionError")
        self.sent.append(message_id)


class CheckStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.checks: list[dict] = []

    async def list_message_checks(self, after, limit):
        return [c for c in self.checks if c["message_id"] > after][:limit]


def _settings():
    return Settings(app_env="aws", db_host="unused", db_port=5432, db_name="unused", db_secret_id="unused",
                    s3_bucket="unused", s3_endpoint_url=None, s3_force_path_style=False, admin_token="test-admin",
                    poll_interval=0.05)


@pytest.fixture
def setup():
    store, events = CheckStore(), FakeEvents()
    with TestClient(create_app(_settings(), store, FakeStorage(), events=events)) as c:
        yield c, store, events


def test_saved_message_sends_only_its_id(setup):
    c, _, events = setup
    r = c.post("/api/messages", json=message(body="카드 1234-5678-9012-3456"))
    assert r.status_code == 201 and events.sent == [r.json()["id"]]


def test_send_failure_is_503_and_same_key_retry_sends_again(setup):
    c, store, events = setup
    m = message()
    events.fail = True
    r = c.post("/api/messages", json=m)
    assert r.status_code == 503 and r.headers["Retry-After"] and events.sent == []
    assert len(store.messages) == 1  # 저장은 됐다. 재요청은 같은 행을 쓴다
    events.fail = False
    again = c.post("/api/messages", json=m)
    assert again.status_code == 200 and events.sent == [again.json()["id"]]
    assert len(store.messages) == 1


def test_retry_after_success_sends_again_and_results_stay_one_per_message(setup):
    c, _, events = setup
    m = message()
    first = c.post("/api/messages", json=m).json()["id"]
    c.post("/api/messages", json=m)
    assert events.sent == [first, first]  # 결과 테이블이 message_id PK라 결과는 한 행이다


def test_check_results_show_kinds_and_counts_only(setup):
    c, store, _ = setup
    store.checks = [{"message_id": 1, "has_pii": True, "kinds": ["card"], "counts": {"card": 1}},
                    {"message_id": 2, "has_pii": False, "kinds": [], "counts": {}}]
    assert c.get("/api/message-checks", params={"after": 1}).json() == [store.checks[1]]
    assert c.get("/api/info").json()["pii_check_enabled"] is True


def test_disabled_without_queue_url():
    store = CheckStore()
    with TestClient(create_app(_settings(), store, FakeStorage())) as c:
        assert c.post("/api/messages", json=message()).status_code == 201
        assert c.get("/api/message-checks").status_code == 404
        assert c.get("/api/info").json()["pii_check_enabled"] is False

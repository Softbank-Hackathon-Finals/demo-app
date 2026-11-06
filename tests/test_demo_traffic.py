import random

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from testing import demo_traffic as bot
from tests.test_api import FakeStorage, FakeStore

# The same literal is decoded by tests/ui-unit/ledger.test.js, so both sides agree.
TRANSFER_LITERAL = (
    "[Bravo 송금] 3,000P · 하늘 → 준서 · 커피\n"
    'BRAVO-LEDGER/1 {"t":"transfer","n":"00000000-0000-4000-8000-00000000000a",'
    '"from":"AAAAAA","to":"BBBBBB","amount":3000,"memo":"커피"}'
)


class ClientHttp:
    """demo_traffic.Http over an in-process app; can block writes after N calls."""

    def __init__(self, client):
        self.client = client
        self.posts = []

    def request(self, method, path, payload=None):
        if method == "POST":
            self.posts.append(payload)
        response = self.client.request(method, path, json=payload)
        body = response.json() if response.content else None
        return response.status_code, dict(response.headers), body


def app_client():
    settings = Settings(
        app_env="aws",
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
    return TestClient(create_app(settings, FakeStore(), FakeStorage()))


def test_wire_format_matches_the_browser_ledger():
    body = bot.encode_transfer(
        "00000000-0000-4000-8000-00000000000a", "AAAAAA", "BBBBBB", 3000, "커피", "하늘", "준서"
    )
    assert body == TRANSFER_LITERAL
    assert bot.decode(body)["amount"] == 3000
    assert bot.decode("평범한 상담 메시지") is None
    assert bot.encode_open("CCCCCC", "자동 참여자 하늘").endswith(
        'BRAVO-LEDGER/1 {"t":"open","acct":"CCCCCC","name":"자동 참여자 하늘"}'
    )


def test_bots_trade_without_overdraft_and_keep_the_total():
    with app_client() as client:
        http = ClientHttp(client)
        logs = []
        assert (
            bot.run(
                http,
                names=["자동 하늘", "자동 준서", "자동 서연"],
                interval=0,
                duration=0.4,
                include_others=False,
                rng=random.Random(7),
                log=logs.append,
                sleep=lambda _: None,
            )
            == 0
        )
        rows = client.get("/api/messages").json()
        ledger = bot.Ledger()
        for row in rows:
            ledger.ingest(row)
            assert not row["author"].startswith("loadgen-")
        assert len(ledger.balance) == 3
        assert sum(ledger.balance.values()) == 3 * bot.GRANT
        assert all(value >= 0 for value in ledger.balance.values())
        assert any("→" in line for line in logs)


def test_a_paused_write_is_retried_with_the_same_key_and_stored_once():
    with app_client() as client:
        http = ClientHttp(client)
        payload = {
            "idempotency_key": "00000000-0000-4000-8000-0000000000bb",
            "author": "자동 하늘",
            "body": bot.encode_open("DDDDDD", "자동 하늘"),
        }
        admin = {"Authorization": "Bearer test-admin"}
        client.put("/admin/write-block", json={"blocked": True}, headers=admin)
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                client.put("/admin/write-block", json={"blocked": False}, headers=admin)

        row, attempts, env = bot.deliver(http, payload, sleep=sleep, log=lambda _: None)
        assert (attempts, env) == (3, "aws")
        assert waits == [2.0, 2.0]  # Retry-After from the write block
        assert all(p == payload for p in http.posts)
        assert len(client.get("/api/messages").json()) == 1
        assert row["id"] == 1


def test_a_rejected_request_is_not_retried():
    with app_client() as client:
        http = ClientHttp(client)
        payload = {"idempotency_key": "not-a-uuid", "author": "x", "body": "y"}
        assert bot.deliver(http, payload, sleep=lambda _: None, log=lambda _: None) is None
        assert len(http.posts) == 1

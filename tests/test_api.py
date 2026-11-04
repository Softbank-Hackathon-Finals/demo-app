import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import IdempotencyConflict, UnknownAttachment

ADMIN = {"Authorization": "Bearer test-admin"}


class FakeStore:
    """Store와 같은 계약을 메모리로 흉내 낸다. 실제 DB 동작은 test_store.py에서 본다."""

    def __init__(self):
        self.messages: list[dict] = []
        self.keys: dict[uuid.UUID, dict] = {}
        self.attachments: dict[uuid.UUID, dict] = {}

    async def ping(self):
        pass

    async def add_message(self, key, author, body, attachment_id):
        if attachment_id is not None and attachment_id not in self.attachments:
            raise UnknownAttachment
        if key in self.keys:
            m = self.keys[key]
            if (m["author"], m["body"], m["attachment_id"]) != (author, body, attachment_id):
                raise IdempotencyConflict
            return m, False
        m = {
            "id": len(self.messages) + 1,
            "author": author,
            "body": body,
            "attachment_id": attachment_id,
            "created_at": datetime.now(UTC),
        }
        self.messages.append(m)
        self.keys[key] = m
        return m, True

    async def list_messages(self, after, limit):
        return [m for m in self.messages if after is None or m["id"] > after][:limit]

    async def add_attachment(self, attachment_id, object_key, filename, content_type, size):
        row = {
            "id": attachment_id,
            "object_key": object_key,
            "filename": filename,
            "content_type": content_type,
            "size": size,
        }
        self.attachments.setdefault(attachment_id, row)
        return row

    async def get_attachment(self, attachment_id):
        return self.attachments.get(attachment_id)


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key, data, content_type):
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]


@pytest.fixture
def client():
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
    with TestClient(create_app(settings, FakeStore(), FakeStorage())) as c:
        yield c


def message(**overrides):
    return {
        "idempotency_key": str(uuid.uuid4()),
        "author": "상담원",
        "body": "안녕하세요",
    } | overrides


def test_same_idempotency_key_is_stored_once(client):
    m = message()
    first = client.post("/api/messages", json=m)
    again = client.post("/api/messages", json=m)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(client.get("/api/messages").json()) == 1


def test_reused_key_with_different_body_conflicts(client):
    m = message()
    client.post("/api/messages", json=m)
    assert client.post("/api/messages", json=m | {"body": "다른 내용"}).status_code == 409


def test_empty_message_is_rejected(client):
    assert client.post("/api/messages", json=message(body="  ")).status_code == 422


def test_write_block_rejects_writes_and_keeps_reads(client):
    client.post("/api/messages", json=message())
    assert (
        client.put("/admin/write-block", json={"blocked": True}, headers=ADMIN).status_code == 200
    )

    blocked = client.post("/api/messages", json=message())
    assert blocked.status_code == 503
    assert blocked.headers["Retry-After"] == "2"
    upload = client.put(f"/api/attachments/{uuid.uuid4()}", files={"file": ("a.txt", b"x")})
    assert upload.status_code == 503
    assert len(client.get("/api/messages").json()) == 1

    client.put("/admin/write-block", json={"blocked": False}, headers=ADMIN)
    assert client.post("/api/messages", json=message()).status_code == 201


def test_write_block_requires_admin_token(client):
    assert client.put("/admin/write-block", json={"blocked": True}).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert (
        client.put("/admin/write-block", json={"blocked": True}, headers=wrong).status_code == 401
    )
    assert client.get("/api/info").json()["write_blocked"] is False


def test_attachment_round_trip(client):
    attachment_id = str(uuid.uuid4())
    png = b"\x89PNG\r\n\x1a\n"
    up = client.put(
        f"/api/attachments/{attachment_id}", files={"file": ("a.png", png, "image/png")}
    )
    assert up.status_code == 200
    sent = client.post("/api/messages", json=message(body="", attachment_id=attachment_id))
    assert sent.status_code == 201
    down = client.get(f"/api/attachments/{attachment_id}")
    assert down.content == png
    assert down.headers["content-type"] == "image/png"


def test_non_image_attachment_is_downloaded_not_rendered(client):
    attachment_id = str(uuid.uuid4())
    client.put(
        f"/api/attachments/{attachment_id}", files={"file": ("x.html", b"<script>", "text/html")}
    )
    down = client.get(f"/api/attachments/{attachment_id}")
    assert down.headers["content-type"] == "application/octet-stream"
    assert down.headers["content-disposition"] == "attachment"


def test_unknown_attachment_is_rejected(client):
    assert (
        client.post("/api/messages", json=message(attachment_id=str(uuid.uuid4()))).status_code
        == 422
    )


def test_websocket_resumes_after_last_id(client):
    for text in ["하나", "둘", "셋"]:
        client.post("/api/messages", json=message(body=text))
    with client.websocket_connect("/ws?after=1") as ws:
        hello = ws.receive_json()
        assert hello == {
            "type": "hello",
            "env": "aws",
            "write_blocked": False,
            "attachment_upload_enabled": True,
        }
        batch = ws.receive_json()
        assert [m["body"] for m in batch["items"]] == ["둘", "셋"]

        client.post("/api/messages", json=message(body="넷"))
        assert [m["body"] for m in ws.receive_json()["items"]] == ["넷"]

        client.put("/admin/write-block", json={"blocked": True}, headers=ADMIN)
        assert ws.receive_json() == {
            "type": "status",
            "env": "aws",
            "write_blocked": True,
            "attachment_upload_enabled": True,
        }

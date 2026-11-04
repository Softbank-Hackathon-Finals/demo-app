import asyncio
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.upload_policy import AttachmentUploadPolicy
from tests.test_api import ADMIN, FakeStorage, FakeStore, message


def settings(**changes):
    return Settings(
        app_env="local",
        db_host="unused",
        db_port=5432,
        db_name="unused",
        db_secret_id="unused",
        s3_bucket="unused",
        s3_endpoint_url=None,
        s3_force_path_style=False,
        admin_token="test-admin",
        poll_interval=0.01,
        **changes,
    )


@pytest.mark.parametrize("value", ["", "TRUE", "False", "0", " false", "false\n"])
def test_ambiguous_upload_environment_fails_startup(monkeypatch, value):
    monkeypatch.setenv("ATTACHMENT_UPLOAD_ENABLED", value)
    with pytest.raises(ValueError, match="ATTACHMENT_UPLOAD_ENABLED"):
        Settings.from_env()


@pytest.mark.parametrize("value,enabled", [(None, True), ("true", True), ("false", False)])
def test_upload_environment(monkeypatch, value, enabled):
    for name in ("DB_HOST", "DB_SECRET_ID", "S3_BUCKET"):
        monkeypatch.setenv(name, "unused")
    if value is None:
        monkeypatch.delenv("ATTACHMENT_UPLOAD_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ATTACHMENT_UPLOAD_ENABLED", value)
    assert Settings.from_env().attachment_upload_enabled is enabled


@pytest.mark.parametrize("value", [1, 0, "false", None])
def test_programmatic_settings_cannot_grant_upload_with_truthy_string(value):
    with pytest.raises(ValueError):
        settings(attachment_upload_enabled=value)


def test_disabled_upload_does_not_consume_body_or_call_application():
    async def run():
        async def forbidden(*args):
            raise AssertionError("body/application must not be accessed")

        sent = []

        async def send(event):
            sent.append(event)

        policy = AttachmentUploadPolicy(forbidden, enabled=False)
        await policy(
            {"type": "http", "method": "PUT", "path": "/api/attachments/anything"},
            forbidden,
            send,
        )
        assert sent[0]["status"] == 403
        assert (b"cache-control", b"no-store") in sent[0]["headers"]

    asyncio.run(run())


def test_freeze_blocks_direct_upload_but_retains_data_reads_and_message_writes():
    store, storage = FakeStore(), FakeStorage()
    enabled = settings()
    attachment_id = str(uuid.uuid4())
    # An existing object is created before the frozen process starts.
    with TestClient(create_app(enabled, store, storage)) as client:
        assert (
            client.put(
                f"/api/attachments/{attachment_id}",
                files={"file": ("old.txt", b"original")},
            ).status_code
            == 200
        )

    with TestClient(
        create_app(replace(enabled, attachment_upload_enabled=False), store, storage)
    ) as client:
        objects, metadata = dict(storage.objects), dict(store.attachments)
        for target in (attachment_id, str(uuid.uuid4())):
            reply = client.put(
                f"/api/attachments/{target}",
                files={"file": ("new.txt", b"replacement")},
            )
            assert reply.status_code == 403
            assert reply.headers["X-Bravo-Env"] == enabled.app_env
            assert "Retry-After" not in reply.headers
        # Malformed multipart is rejected by policy before parsing, too.
        assert (
            client.put(
                f"/api/attachments/{attachment_id}",
                content=b"malformed",
                headers={"Content-Type": "multipart/form-data; boundary=absent"},
            ).status_code
            == 403
        )
        assert storage.objects == objects
        assert store.attachments == metadata
        assert client.get(f"/api/attachments/{attachment_id}").content == b"original"
        assert client.post("/api/messages", json=message()).status_code == 201

        assert (
            client.post("/api/messages", json=message(attachment_id=attachment_id)).status_code
            == 201
        )
        assert client.get("/api/info").json()["attachment_upload_enabled"] is False
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["attachment_upload_enabled"] is False
        # Clearing the cutover write switch cannot re-enable startup upload policy.
        for blocked in (True, False):
            result = client.put("/admin/write-block", headers=ADMIN, json={"blocked": blocked})
            assert result.json() == {"env": "local", "write_blocked": blocked}
            assert client.put(f"/api/attachments/{attachment_id}").status_code == 403
        assert client.post("/api/messages", json=message()).status_code == 201


def test_frozen_upload_has_environment_before_consuming_body():
    async def run():
        store, storage = FakeStore(), FakeStorage()
        app = create_app(settings(attachment_upload_enabled=False), store, storage)

        async def receive():
            raise AssertionError("frozen upload must not read the request body")

        sent = []

        async def send(event):
            sent.append(event)

        await app(
            {
                "type": "http",
                "method": "PUT",
                "path": "/api/attachments/example",
                "headers": [],
                "query_string": b"",
                "http_version": "1.1",
                "scheme": "http",
                "server": ("localhost", 80),
                "client": ("localhost", 1),
            },
            receive,
            send,
        )
        assert sent[0]["status"] == 403
        assert (b"x-bravo-env", b"local") in sent[0]["headers"]
        assert store.attachments == {} and storage.objects == {}

    asyncio.run(run())

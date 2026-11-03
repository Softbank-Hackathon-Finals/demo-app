from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from tests import test_api
from tests.test_api import ADMIN, FakeStorage, FakeStore, message

client = test_api.client


def block(client):
    assert (
        client.put("/admin/write-block", json={"blocked": True}, headers=ADMIN).status_code == 200
    )


def test_close_requires_admin_and_write_block(client):
    assert client.post("/admin/websockets/close").status_code == 401
    assert (
        client.post(
            "/admin/websockets/close", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.post("/admin/websockets/close", headers=ADMIN).status_code == 409
    block(client)
    assert client.post("/admin/websockets/close", headers=ADMIN).json() == {
        "closed": 0,
        "code": 1012,
    }


def test_unconfigured_admin_hides_close_endpoint(client):
    settings = replace(client.app.state.settings, admin_token=None)
    with TestClient(create_app(settings, FakeStore(), FakeStorage())) as other:
        assert other.post("/admin/websockets/close", headers=ADMIN).status_code == 404


def test_closes_current_clients_with_1012_and_reconnect_keeps_last_id(client):
    saved = client.post("/api/messages", json=message(body="before")).json()
    with client.websocket_connect("/ws?after=" + str(saved["id"])) as first:
        with client.websocket_connect("/ws?after=" + str(saved["id"])) as second:
            assert first.receive_json()["type"] == second.receive_json()["type"] == "hello"
            block(client)
            assert first.receive_json()["write_blocked"] is True
            assert second.receive_json()["write_blocked"] is True
            assert client.post("/admin/websockets/close", headers=ADMIN).json() == {
                "closed": 2,
                "code": 1012,
            }
            for socket in (first, second):
                with pytest.raises(WebSocketDisconnect) as disconnected:
                    socket.receive_json()
                assert disconnected.value.code == 1012
            assert client.post("/admin/websockets/close", headers=ADMIN).json() == {
                "closed": 0,
                "code": 1012,
            }
    assert client.get("/api/messages").json()[0]["id"] == saved["id"]
    client.put("/admin/write-block", json={"blocked": False}, headers=ADMIN)
    later = client.post("/api/messages", json=message(body="after")).json()
    with client.websocket_connect("/ws?after=" + str(saved["id"])) as reconnected:
        assert reconnected.receive_json()["type"] == "hello"
        assert [m["id"] for m in reconnected.receive_json()["items"]] == [later["id"]]

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


def test_close_timeout_retry_and_partial_success_use_one_transport_operation(client):
    import asyncio

    import httpx

    async def scenario():
        release_close, release_db = asyncio.Event(), asyncio.Event()
        close_started = asyncio.Event()
        sent = [[], []]
        close_attempts = [0, 0]

        class WaitingStore(FakeStore):
            async def list_messages(self, after, limit):
                await release_db.wait()
                return []

        app = create_app(client.app.state.settings, WaitingStore(), FakeStorage())
        app.state.write_blocked = True
        tasks = []
        try:
            for index in range(2):
                receive_queue = asyncio.Queue()
                await receive_queue.put({"type": "websocket.connect"})

                async def send(event, index=index):
                    if event["type"] == "websocket.close":
                        close_attempts[index] += 1
                        if index == 1:
                            close_started.set()
                            await release_close.wait()
                    sent[index].append(event)

                scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws",
                         "query_string": b"", "headers": [], "scheme": "ws",
                         "server": ("test", 80), "client": ("test", 1234),
                         "subprotocols": []}
                tasks.append(asyncio.create_task(app(scope, receive_queue.get, send)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                while not all(any(e["type"] == "websocket.send" for e in events) for events in sent):
                    await asyncio.sleep(0.001)
                # One client confirms; the other's actual Starlette close send is suspended.
                first = await http.post("/admin/websockets/close", headers=ADMIN)
                assert first.status_code == 503 and close_started.is_set()
                # Concurrent retries observe that same in-flight close, never a second send.
                retries = await asyncio.gather(*(http.post("/admin/websockets/close", headers=ADMIN) for _ in range(2)))
                assert [r.status_code for r in retries] == [503, 503]
                assert close_attempts == [1, 1]
                assert [sum(e["type"] == "websocket.close" for e in events) for events in sent] == [1, 0]
                release_close.set()
                result = await http.post("/admin/websockets/close", headers=ADMIN)
                assert result.status_code == 200
                assert all(next(e for e in events if e["type"] == "websocket.close")["code"] == 1012 for events in sent)
                assert (await http.post("/admin/websockets/close", headers=ADMIN)).json() == {"closed": 0, "code": 1012}
                assert app.state.write_blocked is True and close_attempts == [1, 1]
        finally:
            release_close.set()
            release_db.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_unknown_close_is_stable_503_even_if_websocket_handler_exits(client):
    import asyncio

    import httpx

    async def scenario():
        hello, release_db = asyncio.Event(), asyncio.Event()
        close_attempts = 0

        class WaitingStore(FakeStore):
            async def list_messages(self, after, limit):
                await release_db.wait()
                return []

        app = create_app(client.app.state.settings, WaitingStore(), FakeStorage())
        app.state.write_blocked = True
        receive = asyncio.Queue()
        await receive.put({"type": "websocket.connect"})

        async def send(event):
            nonlocal close_attempts
            if event["type"] == "websocket.send":
                hello.set()
            if event["type"] == "websocket.close":
                close_attempts += 1
                raise RuntimeError("unconfirmed ASGI transport failure")

        scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws", "query_string": b"",
                 "headers": [], "scheme": "ws", "server": ("test", 80), "client": ("test", 1234),
                 "subprotocols": []}
        task = asyncio.create_task(app(scope, receive.get, send))
        try:
            await hello.wait()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                assert (await http.post("/admin/websockets/close", headers=ADMIN)).status_code == 503
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                assert (await http.post("/admin/websockets/close", headers=ADMIN)).status_code == 503
                assert close_attempts == 1 and app.state.write_blocked
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_message_send_and_concurrent_closes_are_serialized():
    import asyncio

    from starlette.websockets import WebSocket

    from app.sockets import SocketSession

    async def scenario():
        release_send, sending = asyncio.Event(), asyncio.Event()
        events = []

        async def send(event):
            if event["type"] == "websocket.send":
                sending.set()
                await release_send.wait()
            events.append(event)

        receive = asyncio.Queue()
        await receive.put({"type": "websocket.connect"})
        socket = WebSocket({"type": "websocket"}, receive.get, send)
        await socket.accept()
        session = SocketSession(socket)
        message_task = asyncio.create_task(session.send_json({"message": "before"}))
        await sending.wait()
        first = asyncio.create_task(session.close_for_cutover())
        second = asyncio.create_task(session.close_for_cutover())
        await asyncio.sleep(0)
        release_send.set()
        await asyncio.gather(message_task, first, second)
        await session.send_json({"message": "must not send"})
        assert [e["type"] for e in events] == ["websocket.accept", "websocket.send", "websocket.close"]
        assert events[-1]["code"] == 1012
        assert await session.close_for_cutover() is False

    asyncio.run(scenario())

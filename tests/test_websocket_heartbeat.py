import asyncio
import json

from app.main import create_app
from tests import test_api
from tests.test_api import FakeStorage, FakeStore

client = test_api.client


def test_idle_connection_advertises_and_emits_status_without_writes(client, monkeypatch):
    import app.main as main_module

    clock = {"now": 0.0}
    monkeypatch.setattr(main_module, "monotonic", lambda: clock["now"], raising=False)

    async def scenario():
        hello, heartbeat = asyncio.Event(), asyncio.Event()
        sent = []

        class ReadOnlyStore(FakeStore):
            async def list_messages(self, after, limit):
                clock["now"] = 6.0
                return []

            async def add_message(self, *args):
                raise AssertionError("heartbeat must not write")

        app = create_app(client.app.state.settings, ReadOnlyStore(), FakeStorage())
        receive = asyncio.Queue()
        await receive.put({"type": "websocket.connect"})

        async def send(event):
            if event["type"] == "websocket.send":
                value = json.loads(event["text"])
                sent.append(value)
                if value["type"] == "hello":
                    hello.set()
                if value["type"] == "status":
                    heartbeat.set()

        scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws", "query_string": b"",
                 "headers": [], "scheme": "ws", "server": ("test", 80), "client": ("test", 1234),
                 "subprotocols": []}
        task = asyncio.create_task(app(scope, receive.get, send))
        try:
            await asyncio.wait_for(hello.wait(), timeout=1)
            assert sent[0]["heartbeat_interval_seconds"] == 5
            await asyncio.wait_for(heartbeat.wait(), timeout=1)
            assert sent[1]["env"] == "aws" and sent[1]["write_blocked"] is False
            assert not app.state.store.messages
        finally:
            await receive.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())

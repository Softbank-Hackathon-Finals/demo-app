"""Loopback-only UI test host; both apps share an in-memory test store.

This exercises actual HTTP/WebSocket clients, not EKS/RKE2 migration or database
durability. It is outside app/ and is never copied into the service image.
"""

import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import Settings
from app.main import create_app
from tests.test_api import FakeStorage, FakeStore


class BrowserStore(FakeStore):
    def enrich(self, row):
        result = dict(row)
        if row["attachment_id"] in self.attachments:
            attachment = self.attachments[row["attachment_id"]]
            result.update(
                attachment_name=attachment["filename"],
                attachment_type=attachment["content_type"],
            )
        return result

    async def list_messages(self, after, limit):
        return [self.enrich(row) for row in await super().list_messages(after, limit)]

    async def add_message(self, *args):
        row, created = await super().add_message(*args)
        return self.enrich(row), created


def browser_app():
    if os.environ.get("BRAVO_BROWSER_TEST") != "1":
        raise RuntimeError("explicit test host opt-in required")
    store, storage = BrowserStore(), FakeStorage()
    apps = {}
    for env in ("aws", "onprem", "upload-blocked"):
        settings = Settings(
            app_env=env,
            db_host="unused",
            db_port=5432,
            db_name="unused",
            db_secret_id="unused",
            s3_bucket="unused",
            s3_endpoint_url=None,
            s3_force_path_style=False,
            admin_token="browser-test-only",
            poll_interval=0.1,
            attachment_upload_enabled=env != "upload-blocked",
        )
        apps[env] = create_app(settings, store, storage)
    selected = "aws"
    sockets = set()

    @asynccontextmanager
    async def lifespan(_):
        async with AsyncExitStack() as stack:
            for app in apps.values():
                await stack.enter_async_context(app.router.lifespan_context(app))
            yield

    controls = FastAPI(lifespan=lifespan)

    @controls.post("/__test__/reset")
    async def reset():
        nonlocal selected
        selected = "aws"
        for app in apps.values():
            app.state.write_blocked = False
        store.messages.clear()
        store.keys.clear()
        store.attachments.clear()
        storage.objects.clear()
        return {"ok": True}

    @controls.post("/__test__/switch/{environment}")
    async def switch(environment: str):
        nonlocal selected
        if environment not in apps:
            raise HTTPException(400)
        selected = environment
        for close in list(sockets):
            await close()
        return {"environment": selected, "scope": "in-memory UI host"}

    async def router(scope, receive, send):
        if scope["type"] == "lifespan" or scope.get("path", "").startswith("/__test__/"):
            await controls(scope, receive, send)
            return
        app = apps[selected]
        if scope["type"] != "websocket":
            await app(scope, receive, send)
            return
        closed = False

        async def close():
            nonlocal closed
            if not closed:
                closed = True
                await send({"type": "websocket.close", "code": 1012})

        async def guarded_send(message):
            if not closed:
                await send(message)

        sockets.add(close)
        try:
            await app(scope, receive, guarded_send)
        finally:
            sockets.discard(close)

    return router

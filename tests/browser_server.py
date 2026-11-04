"""Loopback-only UI test host; apps share memory or an owned temporary PostgreSQL.

This is not EKS/RKE2 or database migration. Attachment bytes remain a test double.
It is outside app/ and is never copied into the service image.
"""

import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import Settings
from app.main import create_app
from tests.browser_database import BrowserDatabase
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
    database = None
    if fixture := os.environ.get("BRAVO_BROWSER_DATABASE_FIXTURE"):
        database = BrowserDatabase(fixture)
    store = database.store if database else BrowserStore()
    storage = FakeStorage()
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
    lose_upload_ack = False
    sockets = set()

    @asynccontextmanager
    async def lifespan(_):
        async with AsyncExitStack() as stack:
            if database:
                stack.push_async_callback(database.close)
                await database.open()
            for app in apps.values():
                await stack.enter_async_context(app.router.lifespan_context(app))
            yield

    controls = FastAPI(lifespan=lifespan)

    @controls.post("/__test__/reset")
    async def reset():
        nonlocal selected, lose_upload_ack
        selected = "aws"
        lose_upload_ack = False
        for app in apps.values():
            app.state.write_blocked = False
        if database:
            await database.reset()
        else:
            store.messages.clear()
            store.keys.clear()
            store.attachments.clear()
        storage.objects.clear()
        return {"ok": True}

    @controls.post("/__test__/lose-next-upload-ack")
    async def lose_next_upload_ack():
        nonlocal lose_upload_ack
        lose_upload_ack = True
        return {"armed": True}

    @controls.post("/__test__/switch/{environment}")
    async def switch(environment: str):
        nonlocal selected
        if environment not in apps:
            raise HTTPException(400)
        selected = environment
        for close in list(sockets):
            await close()
        return {
            "environment": selected,
            "scope": "local PostgreSQL UI host" if database else "in-memory UI host",
        }

    async def router(scope, receive, send):
        nonlocal lose_upload_ack
        if scope["type"] == "lifespan" or scope.get("path", "").startswith("/__test__/"):
            await controls(scope, receive, send)
            return
        app = apps[selected]
        if scope["type"] != "websocket":
            if (
                lose_upload_ack
                and scope["type"] == "http"
                and scope.get("method") == "PUT"
                and scope.get("path", "").startswith("/api/attachments/")
            ):
                lose_upload_ack = False
                saved = False

                async def replace_ack(message):
                    nonlocal saved
                    if message["type"] == "http.response.start":
                        saved = 200 <= message["status"] < 300
                        if saved:
                            return
                    if saved and message["type"] == "http.response.body":
                        if not message.get("more_body", False):
                            await send(
                                {
                                    "type": "http.response.start",
                                    "status": 503,
                                    "headers": [(b"retry-after", b"1")],
                                }
                            )
                            await send({"type": "http.response.body", "body": b""})
                        return
                    await send(message)

                # Consume the browser's original multipart request unchanged.
                # Only the successful acknowledgement is replaced, after storage.
                await app(scope, receive, replace_ack)
            else:
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

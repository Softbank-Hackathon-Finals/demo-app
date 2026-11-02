import asyncio
import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, Field, model_validator

from app.config import Settings
from app.db_secret import fetch_db_credentials
from app.storage import AttachmentStorage
from app.store import IdempotencyConflict, Store, UnknownAttachment

log = logging.getLogger("demo-chat")

STATIC_DIR = Path(__file__).parent / "static"
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PAGE_SIZE = 200


class NewMessage(BaseModel):
    idempotency_key: uuid.UUID
    author: str = Field(min_length=1, max_length=40)
    body: str = Field(default="", max_length=2000)
    attachment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def has_content(self):
        if not self.body.strip() and self.attachment_id is None:
            raise ValueError("body or attachment_id is required")
        return self


class WriteBlock(BaseModel):
    blocked: bool


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    storage: AttachmentStorage | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if app.state.store is None:
            user, password = fetch_db_credentials(settings.db_secret_id)
            conninfo = make_conninfo(
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=user,
                password=password,
                application_name=f"demo-chat-{settings.app_env}",
            )
            # PgBouncer transaction 모드에서는 서버 쪽 prepared statement를 쓸 수 없다.
            pool = AsyncConnectionPool(
                conninfo, min_size=1, max_size=10, kwargs={"prepare_threshold": None}, open=False
            )
            await pool.open()
            app.state.store = Store(pool, settings.tx_timeout_ms)
        if app.state.storage is None:
            app.state.storage = AttachmentStorage(
                settings.s3_bucket, settings.s3_endpoint_url, settings.s3_force_path_style
            )
        yield
        if pool is not None:
            await pool.close()

    app = FastAPI(title="demo-chat", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.storage = storage
    # 쓰기 차단 스위치는 프로세스마다 따로 둔다. 컷오버 중에는 DB가 멈춰 있어 DB에 둘 수 없다.
    # Executor는 모든 인스턴스(진입점)에 스위치를 켠다.
    app.state.write_blocked = os.environ.get("WRITE_BLOCKED", "false") == "true"

    def require_writable() -> None:
        if app.state.write_blocked:
            raise HTTPException(
                503,
                "writes are temporarily blocked",
                headers={"Retry-After": str(settings.retry_after)},
            )

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        if settings.admin_token is None:
            raise HTTPException(404)
        expected = f"Bearer {settings.admin_token}".encode()
        if authorization is None or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401)

    def status() -> dict:
        return {"env": settings.app_env, "write_blocked": app.state.write_blocked}

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        try:
            await app.state.store.ping()
        except (OperationalError, PoolTimeout) as e:
            raise HTTPException(503, "database unavailable") from e
        return {"ok": True}

    @app.get("/api/info")
    async def info():
        return status()

    @app.get("/api/messages")
    async def list_messages(after: int | None = None):
        return jsonable_encoder(await app.state.store.list_messages(after, PAGE_SIZE))

    @app.post("/api/messages", dependencies=[Depends(require_writable)])
    async def post_message(message: NewMessage):
        try:
            saved, created = await app.state.store.add_message(
                message.idempotency_key, message.author, message.body, message.attachment_id
            )
        except IdempotencyConflict as e:
            raise HTTPException(409, "idempotency key reused with different content") from e
        except UnknownAttachment as e:
            raise HTTPException(422, "unknown attachment") from e
        return JSONResponse(jsonable_encoder(saved), status_code=201 if created else 200)

    @app.put("/api/attachments/{attachment_id}", dependencies=[Depends(require_writable)])
    async def put_attachment(attachment_id: uuid.UUID, file: Annotated[UploadFile, File()]):
        data = await file.read(settings.max_attachment_bytes + 1)
        if len(data) > settings.max_attachment_bytes:
            raise HTTPException(413, "attachment too large")
        filename = Path(file.filename or "file").name[:200]
        content_type = file.content_type or "application/octet-stream"
        object_key = f"attachments/{attachment_id}"
        # 같은 id로 재시도하면 같은 객체를 덮어쓰므로 멱등이다.
        await run_in_threadpool(app.state.storage.put, object_key, data, content_type)
        try:
            saved = await app.state.store.add_attachment(
                attachment_id, object_key, filename, content_type, len(data)
            )
        except IdempotencyConflict as e:
            raise HTTPException(409, "attachment id reused with different content") from e
        return jsonable_encoder(saved)

    @app.get("/api/attachments/{attachment_id}")
    async def get_attachment(attachment_id: uuid.UUID):
        meta = await app.state.store.get_attachment(attachment_id)
        if meta is None:
            raise HTTPException(404)
        data = await run_in_threadpool(app.state.storage.get, meta["object_key"])
        inline = meta["content_type"] in INLINE_TYPES
        return Response(
            data,
            media_type=meta["content_type"] if inline else "application/octet-stream",
            headers={
                "Content-Disposition": "inline" if inline else "attachment",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/admin/write-block", dependencies=[Depends(require_admin)])
    async def get_write_block():
        return status()

    @app.put("/admin/write-block", dependencies=[Depends(require_admin)])
    async def set_write_block(change: WriteBlock, request: Request):
        if app.state.write_blocked != change.blocked:
            app.state.write_blocked = change.blocked
            log.warning("write block %s by %s", "on" if change.blocked else "off", request.client)
        return status()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, after: int | None = None):
        """마지막 메시지 id 이후를 DB에서 주기적으로 조회해 보낸다.

        두 환경의 앱이 동시에 떠 있어도 공통 쓰기 DB를 기준으로 서로의 메시지가 보인다(설계 문서 8.10).
        서버 메모리 · pub/sub · LISTEN/NOTIFY는 쓰지 않는다.
        """
        await websocket.accept()
        await websocket.send_json({"type": "hello", **status()})
        sent_status = status()
        receiver = asyncio.create_task(_drain(websocket))
        last_id = after
        try:
            while not receiver.done():
                try:
                    items = await app.state.store.list_messages(last_id, PAGE_SIZE)
                except (OperationalError, PoolTimeout):
                    # 컷오버 중 DB가 잠시 멈추거나 바뀔 수 있다. 연결은 유지하고 다음 주기에 다시 조회한다.
                    log.warning("poll failed, retrying", exc_info=True)
                    items = None
                if items:
                    last_id = items[-1]["id"]
                    await websocket.send_json(
                        {"type": "messages", "items": jsonable_encoder(items)}
                    )
                elif items is not None and last_id is None:
                    last_id = 0
                if status() != sent_status:
                    sent_status = status()
                    await websocket.send_json({"type": "status", **sent_status})
                if items is None or len(items) < PAGE_SIZE:
                    await asyncio.wait({receiver}, timeout=settings.poll_interval)
        except WebSocketDisconnect:
            pass
        finally:
            receiver.cancel()

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


async def _drain(websocket: WebSocket) -> None:
    """클라이언트가 보내는 것은 무시하고, 연결이 끊기면 끝난다."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass

import uuid
from contextlib import asynccontextmanager

from psycopg import errors, pq
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# 메시지 쓰기를 직렬화하는 advisory lock 키. 값 자체에는 의미가 없다.
MESSAGE_WRITE_LOCK = 4_310_001

MESSAGE_COLUMNS = """
    m.id, m.author, m.body, m.created_at, m.attachment_id,
    a.filename AS attachment_name, a.content_type AS attachment_type, a.size AS attachment_size
"""


class IdempotencyConflict(Exception):
    """같은 멱등 키로 다른 내용을 보냈다."""


class UnknownAttachment(Exception):
    """메시지가 가리키는 첨부가 없다."""


class Store:
    def __init__(self, pool: AsyncConnectionPool, tx_timeout_ms: int):
        self.pool = pool
        self.tx_timeout = f"{tx_timeout_ms}ms"

    @asynccontextmanager
    async def _autocommit(self):
        """BEGIN · COMMIT 왕복 없이 보내는 연결. 풀에 돌려줄 때 원래 설정으로 되돌린다."""
        async with self.pool.connection() as conn:
            original_autocommit = conn.autocommit
            await conn.set_autocommit(True)
            try:
                yield conn
            finally:
                # 끊긴 연결은 풀이 버린다. 여기서 또 예외를 내면 503이 500으로 바뀐다.
                if conn.pgconn.transaction_status == pq.TransactionStatus.IDLE:
                    await conn.set_autocommit(original_autocommit)

    async def ping(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")

    async def add_message(
        self, key: uuid.UUID, author: str, body: str, attachment_id: uuid.UUID | None
    ) -> tuple[dict, bool]:
        """메시지를 저장하고 (메시지, 새로 저장했는지)를 돌려준다. 같은 멱등 키의 재전송은 기존 메시지를 돌려준다."""
        async with self._autocommit() as conn, conn.cursor(row_factory=dict_row) as saved:
            try:
                # 잠금부터 커밋까지 응답을 기다리지 않고 한 묶음으로 보낸다. 묶음 끝(Sync)에서 함께
                # 커밋되므로 잠금을 쥔 동안 DB까지 왕복이 없고, 하나가 실패하면 묶음 전체가 취소된다.
                async with conn.pipeline():
                    # 트랜잭션 범위 설정이라 PgBouncer transaction 모드에서도 안전하다.
                    await conn.execute(
                        "SELECT set_config('statement_timeout', %s, true)", (self.tx_timeout,)
                    )
                    # 커밋 순서를 id 순서와 맞춘다. 전파가 "마지막 id 이후"를 조회하므로,
                    # 늦게 커밋된 작은 id를 구독자가 건너뛰지 않게 한다.
                    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MESSAGE_WRITE_LOCK,))
                    inserted = await conn.execute(
                        """
                        INSERT INTO messages (idempotency_key, author, body, attachment_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING
                        RETURNING id
                        """,
                        (key, author, body, attachment_id),
                    )
                    await saved.execute(
                        f"SELECT {MESSAGE_COLUMNS} FROM messages m"
                        " LEFT JOIN attachments a ON a.id = m.attachment_id"
                        " WHERE m.idempotency_key = %s",
                        (key,),
                    )
            except errors.ForeignKeyViolation as e:
                raise UnknownAttachment from e
            created = await inserted.fetchone() is not None
            message = await saved.fetchone()
            # 이미 있던 메시지와 내용이 다르면 충돌이다. 이때 위 묶음은 아무것도 쓰지 않았다.
            if not created and (
                message["author"],
                message["body"],
                message["attachment_id"],
            ) != (author, body, attachment_id):
                raise IdempotencyConflict
            return message, created

    async def list_messages(self, after: int | None, limit: int) -> list[dict]:
        """after 이후 메시지를 id 순으로 돌려준다. after가 없으면 최근 limit개."""
        # 읽기에 트랜잭션이 필요 없다. BEGIN · COMMIT 왕복을 없애 풀 연결을 그만큼 덜 쥔다.
        async with self._autocommit() as conn, conn.cursor(row_factory=dict_row) as cur:
            if after is None:
                await cur.execute(
                    f"SELECT * FROM (SELECT {MESSAGE_COLUMNS} FROM messages m"
                    " LEFT JOIN attachments a ON a.id = m.attachment_id"
                    " ORDER BY m.id DESC LIMIT %s) recent ORDER BY id",
                    (limit,),
                )
            else:
                await cur.execute(
                    f"SELECT {MESSAGE_COLUMNS} FROM messages m"
                    " LEFT JOIN attachments a ON a.id = m.attachment_id"
                    " WHERE m.id > %s ORDER BY m.id LIMIT %s",
                    (after, limit),
                )
            return await cur.fetchall()

    async def add_attachment(
        self, attachment_id: uuid.UUID, object_key: str, filename: str, content_type: str, size: int
    ) -> dict:
        """첨부 메타데이터를 저장한다. 클라이언트가 만든 id가 멱등 키 역할을 한다."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT set_config('statement_timeout', %s, true)", (self.tx_timeout,)
            )
            await cur.execute(
                """
                INSERT INTO attachments (id, object_key, filename, content_type, size)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (attachment_id, object_key, filename, content_type, size),
            )
            await cur.execute(
                "SELECT id, filename, content_type, size FROM attachments WHERE id = %s",
                (attachment_id,),
            )
            row = await cur.fetchone()
            if (row["filename"], row["content_type"], row["size"]) != (
                filename,
                content_type,
                size,
            ):
                raise IdempotencyConflict
            return row

    async def get_attachment(self, attachment_id: uuid.UUID) -> dict | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, object_key, filename, content_type, size FROM attachments WHERE id = %s",
                (attachment_id,),
            )
            return await cur.fetchone()

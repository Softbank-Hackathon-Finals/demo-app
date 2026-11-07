import uuid

from psycopg import errors
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

    async def ping(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")

    async def add_message(
        self, key: uuid.UUID, author: str, body: str, attachment_id: uuid.UUID | None
    ) -> tuple[dict, bool]:
        """메시지를 저장하고 (메시지, 새로 저장했는지)를 돌려준다. 같은 멱등 키의 재전송은 기존 메시지를 돌려준다."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            # 트랜잭션 범위 설정이라 PgBouncer transaction 모드에서도 안전하다.
            await cur.execute(
                "SELECT set_config('statement_timeout', %s, true)", (self.tx_timeout,)
            )
            # 커밋 순서를 id 순서와 맞춘다. 전파가 "마지막 id 이후"를 조회하므로,
            # 늦게 커밋된 작은 id를 구독자가 건너뛰지 않게 한다.
            await cur.execute("SELECT pg_advisory_xact_lock(%s)", (MESSAGE_WRITE_LOCK,))
            try:
                await cur.execute(
                    """
                    INSERT INTO messages (idempotency_key, author, body, attachment_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """,
                    (key, author, body, attachment_id),
                )
            except errors.ForeignKeyViolation as e:
                raise UnknownAttachment from e
            inserted = await cur.fetchone()
            if inserted is None:
                await cur.execute(
                    "SELECT id, author, body, attachment_id FROM messages WHERE idempotency_key = %s",
                    (key,),
                )
                existing = await cur.fetchone()
                if (existing["author"], existing["body"], existing["attachment_id"]) != (
                    author,
                    body,
                    attachment_id,
                ):
                    raise IdempotencyConflict
                message_id = existing["id"]
            else:
                message_id = inserted["id"]
            await cur.execute(
                f"SELECT {MESSAGE_COLUMNS} FROM messages m"
                " LEFT JOIN attachments a ON a.id = m.attachment_id WHERE m.id = %s",
                (message_id,),
            )
            return await cur.fetchone(), inserted is not None

    async def list_messages(self, after: int | None, limit: int) -> list[dict]:
        """after 이후 메시지를 id 순으로 돌려준다. after가 없으면 최근 limit개."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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

    async def list_message_checks(self, after: int, limit: int) -> list[dict]:
        """after 이후 메시지의 개인정보 검사 결과(T46). 종류와 개수만 있고 일치한 값은 없다."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT message_id, has_pii, kinds, counts FROM message_checks"
                " WHERE message_id > %s ORDER BY message_id LIMIT %s",
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

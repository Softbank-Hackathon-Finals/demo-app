"""실제 Postgres에서 Store를 확인한다. TEST_DATABASE_URL(스키마 적용된 DB, chat_app 역할)이 있을 때만 돈다.

docker compose up -d db
TEST_DATABASE_URL=postgresql://chat_app:local-only-app@localhost:5432/chat uv run pytest
"""

import asyncio
import os
import uuid

import pytest
from psycopg import AsyncConnection, errors, pq
from psycopg_pool import AsyncConnectionPool

import app.store as store_module
from app.store import MESSAGE_WRITE_LOCK, IdempotencyConflict, Store, UnknownAttachment

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def run(coro_fn):
    async def main():
        async with AsyncConnectionPool(DSN, kwargs={"prepare_threshold": None}) as pool:
            await coro_fn(Store(pool, 200))

    asyncio.run(main())


def test_idempotent_insert():
    async def check(store):
        key = uuid.uuid4()
        first, created = await store.add_message(key, "a", "hello", None)
        again, created_again = await store.add_message(key, "a", "hello", None)
        assert (created, created_again) == (True, False)
        assert first["id"] == again["id"]
        with pytest.raises(IdempotencyConflict):
            await store.add_message(key, "a", "changed", None)

    run(check)


def test_unknown_attachment():
    async def check(store):
        with pytest.raises(UnknownAttachment):
            await store.add_message(uuid.uuid4(), "a", "x", uuid.uuid4())

    run(check)


def test_concurrent_writes_are_visible_in_id_order():
    """동시 쓰기에서도 "마지막 id 이후" 조회가 메시지를 건너뛰지 않는다."""

    async def check(store):
        start = (await store.list_messages(None, 1) or [{"id": 0}])[-1]["id"]
        seen, last = [], start

        async def poll_until(n):
            nonlocal last
            while len(seen) < n:
                items = await store.list_messages(last, 200)
                seen.extend(m["id"] for m in items)
                if items:
                    last = items[-1]["id"]
                await asyncio.sleep(0)

        writes = [store.add_message(uuid.uuid4(), "w", str(i), None) for i in range(50)]
        async with asyncio.timeout(10):
            results, _ = await asyncio.gather(asyncio.gather(*writes), poll_until(50))
        assert sorted(seen) == sorted(m["id"] for m, _ in results)

    run(check)


def test_acknowledgment_follows_commit():
    async def check(store):
        key = uuid.uuid4()
        message, created = await store.add_message(key, "a", "committed", None)
        assert created
        async with await AsyncConnection.connect(DSN, autocommit=True) as reader:
            row = await (
                await reader.execute("SELECT id FROM messages WHERE idempotency_key = %s", (key,))
            ).fetchone()
        assert row == (message["id"],)

    run(check)


def test_error_after_insert_rolls_back_pipeline(monkeypatch):
    async def check(store):
        key = uuid.uuid4()
        with monkeypatch.context() as patch:
            patch.setattr(store_module, "MESSAGE_COLUMNS", "m.nonexistent_column")
            with pytest.raises(errors.UndefinedColumn):
                await store.add_message(key, "a", "retry", None)
        async with await AsyncConnection.connect(DSN, autocommit=True) as reader:
            row = await (
                await reader.execute(
                    "SELECT count(*) FROM messages WHERE idempotency_key = %s", (key,)
                )
            ).fetchone()
        assert row == (0,), "failed pipeline committed the INSERT"
        _, created = await store.add_message(key, "a", "retry", None)
        assert created

    run(check)


@pytest.mark.parametrize("autocommit", [False, True])
def test_connection_settings_restored(autocommit):
    async def check():
        async with AsyncConnectionPool(
            DSN,
            min_size=1,
            max_size=1,
            kwargs={"prepare_threshold": None, "autocommit": autocommit},
        ) as pool:
            store = Store(pool, 200)
            await store.add_message(uuid.uuid4(), "a", "settings", None)
            await store.list_messages(None, 1)
            async with pool.connection() as conn:
                assert conn.autocommit is autocommit
                assert conn.info.transaction_status == pq.TransactionStatus.IDLE
                timeout = await (await conn.execute("SHOW statement_timeout")).fetchone()
                assert timeout == ("0",), "transaction-local timeout leaked into the pool"

    asyncio.run(check())


def test_lock_timeout_rolls_back_and_pool_recovers():
    async def check(store):
        key = uuid.uuid4()
        store.tx_timeout = "50ms"
        async with await AsyncConnection.connect(DSN) as holder:
            await holder.execute("SELECT pg_advisory_xact_lock(%s)", (MESSAGE_WRITE_LOCK,))
            with pytest.raises(errors.QueryCanceled):
                await store.add_message(key, "a", "blocked", None)
        _, created = await store.add_message(key, "a", "blocked", None)
        assert created, "timed-out transaction wrote before obtaining the lock"

    run(check)


def test_cancellation_does_not_leave_pool_in_transaction():
    async def check():
        async with AsyncConnectionPool(
            DSN,
            min_size=1,
            max_size=1,
            kwargs={"prepare_threshold": None},
        ) as pool:
            store = Store(pool, 2000)
            key = uuid.uuid4()
            async with await AsyncConnection.connect(DSN) as holder:
                await holder.execute("SELECT pg_advisory_xact_lock(%s)", (MESSAGE_WRITE_LOCK,))
                pending = asyncio.create_task(store.add_message(key, "a", "cancel", None))
                try:
                    async with asyncio.timeout(2):
                        while True:
                            row = await (
                                await holder.execute(
                                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                                    " AND classid = 0 AND objid = %s AND NOT granted",
                                    (MESSAGE_WRITE_LOCK,),
                                )
                            ).fetchone()
                            if row[0]:
                                break
                            await asyncio.sleep(0.01)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            async with asyncio.timeout(5):
                _, created = await store.add_message(key, "a", "cancel", None)
            assert created
            async with pool.connection() as conn:
                assert not conn.autocommit
                assert conn.info.transaction_status == pq.TransactionStatus.IDLE

    asyncio.run(check())


def test_write_gets_its_id_after_taking_the_lock():
    """잠금보다 id를 먼저 받으면 커밋 순서가 id 순서와 어긋나 전파가 메시지를 건너뛴다.

    지연이 없으면 위 동시 쓰기 시험은 이 순서가 깨져도 대개 통과한다. 그래서 순서를 직접 만든다.
    """

    async def check(store):
        store.tx_timeout = "5s"  # 잠금을 일부러 쥐고 있는 동안 쓰기가 타임아웃되지 않게 한다.
        async with store.pool.connection() as holder:
            await holder.execute("SELECT pg_advisory_xact_lock(%s)", (MESSAGE_WRITE_LOCK,))
            waiting = asyncio.create_task(store.add_message(uuid.uuid4(), "w", "later", None))
            try:
                async with asyncio.timeout(2):
                    while True:
                        assert not waiting.done(), "잠금을 쥔 동안 쓰기가 끝났다"
                        row = await (
                            await holder.execute(
                                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                                " AND classid = 0 AND objid = %s AND NOT granted",
                                (MESSAGE_WRITE_LOCK,),
                            )
                        ).fetchone()
                        if row[0]:
                            break
                        await asyncio.sleep(0.01)
                cursor = await holder.execute(
                    "INSERT INTO messages (idempotency_key, author, body) VALUES (%s, 'w', 'first') RETURNING id",
                    (uuid.uuid4(),),
                )
                first = (await cursor.fetchone())[0]
            except BaseException:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
                raise
        async with asyncio.timeout(5):
            later, _ = await waiting
        assert later["id"] > first, "잠금을 잡기 전에 id를 받았다"

    run(check)

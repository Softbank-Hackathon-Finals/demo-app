"""실제 Postgres에서 Store를 확인한다. TEST_DATABASE_URL(스키마 적용된 DB, chat_app 역할)이 있을 때만 돈다.

docker compose up -d db
TEST_DATABASE_URL=postgresql://chat_app:local-only-app@localhost:5432/chat uv run pytest
"""

import asyncio
import os
import uuid

import pytest
from psycopg_pool import AsyncConnectionPool

from app.store import IdempotencyConflict, Store, UnknownAttachment

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
        results, _ = await asyncio.gather(asyncio.gather(*writes), poll_until(50))
        assert sorted(seen) == sorted(m["id"] for m, _ in results)

    run(check)

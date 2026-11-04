"""Only the private Unix-socket database created by testing/browser_database.py."""

import json
import os
import re
import stat
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from app.store import Store


class BrowserDatabase:
    def __init__(self, config_path: str):
        path = Path(config_path)
        if path.is_symlink():
            raise ValueError("private browser fixture required")
        info = path.stat()
        root = path.resolve().parent
        root_info = root.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or root_info.st_uid != os.getuid()
            or root_info.st_mode & 0o077
            or not root.name.startswith("bravo-browser-pg-")
        ):
            raise ValueError("private browser fixture required")
        config = json.loads(path.read_text())
        if (
            set(config) != {"database"}
            or not isinstance(config["database"], str)
            or not re.fullmatch(r"bravo_browser_[a-f0-9]{16}", config["database"])
        ):
            raise ValueError("isolated browser database required")
        self.database = config["database"]
        self.admin_dsn = make_conninfo(host=str(root), dbname=self.database, connect_timeout=3)
        self.pool = AsyncConnectionPool(
            make_conninfo(self.admin_dsn, user="chat_app"),
            kwargs={"prepare_threshold": None},
            open=False,
        )
        self.store = Store(self.pool, 200)

    async def open(self):
        await self.pool.open(wait=True)
        async with self.pool.connection() as c:
            row = await (
                await c.execute(
                    "SELECT current_database(), current_user, rolsuper, rolcreatedb, rolcreaterole, "
                    "rolreplication FROM pg_roles WHERE rolname = current_user"
                )
            ).fetchone()
            if row != (self.database, "chat_app", False, False, False, False):
                raise ValueError("non-admin browser app role required")

    async def reset(self):
        # This admin connection cannot point at a remote address or arbitrary DB.
        async with await AsyncConnection.connect(self.admin_dsn) as c:
            await c.execute("TRUNCATE messages, attachments RESTART IDENTITY")

    async def close(self):
        await self.pool.close()

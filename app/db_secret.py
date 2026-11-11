import asyncio
import json
import time
from collections.abc import Callable


DEFAULT_CREDENTIALS_FILE = "/run/bravo/database/credentials.json"


def fetch_db_credentials(credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> tuple[str, str]:
    """Read ESO-injected credentials; never fall back to SDK or environment secrets."""
    try:
        with open(credentials_file, encoding="utf-8") as stream:
            secret = json.load(stream)
        if not isinstance(secret, dict):
            raise ValueError
        username = secret.get("username")
        password = secret.get("password")
        if not all(
            isinstance(value, str) and value.strip() and "\x00" not in value
            for value in (username, password)
        ):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise RuntimeError("Database credentials unavailable or invalid") from None
    return username, password


class DbCredentials:
    """Cache credentials for new connections, refreshing on expiry or invalidation."""

    def __init__(self, fetch: Callable[[], tuple[str, str]], ttl_seconds: float = 300):
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._value: tuple[str, str] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> tuple[str, str]:
        async with self._lock:
            if self._value is None or time.monotonic() - self._fetched_at > self._ttl:
                self._value = None
                self._value = await asyncio.to_thread(self._fetch)
                self._fetched_at = time.monotonic()
            return self._value

    def invalidate(self) -> None:
        self._value = None

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

# OpenBao·ESO(리허설은 init container)가 주입하는 DB 자격 파일
CREDENTIALS_FILE = Path("/run/bravo/database/credentials.json")


def fetch_db_credentials(secret_id: str | None = None) -> tuple[str, str]:
    """DB 사용자와 비밀번호를 주입된 자격 파일에서 읽는다.

    파일이 없거나 형식이 맞지 않으면 값을 남기지 않고 실패한다(fail closed).
    `secret_id`는 기존 호출과 맞추려고 받기만 하고 쓰지 않는다. Secrets Manager는 부르지 않는다.
    """
    try:
        secret = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        user, password = secret["username"], secret["password"]
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("db_credentials_unavailable") from None
    if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
        raise RuntimeError("db_credentials_unavailable")
    return user, password


class DbCredentials:
    """새 DB 연결마다 쓸 자격 증명. 회전에 대비해 주기적으로, 인증 실패 시에는 즉시 다시 읽는다.

    기존 연결은 비밀번호가 바뀌어도 유지되므로, 이 캐시는 새 연결에만 영향을 준다.
    """

    def __init__(self, fetch: Callable[[], tuple[str, str]], ttl_seconds: float = 300):
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._value: tuple[str, str] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> tuple[str, str]:
        async with self._lock:
            if self._value is None or time.monotonic() - self._fetched_at > self._ttl:
                self._value = await asyncio.to_thread(self._fetch)
                self._fetched_at = time.monotonic()
            return self._value

    def invalidate(self) -> None:
        self._value = None

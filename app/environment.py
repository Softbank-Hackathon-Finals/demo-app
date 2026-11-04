"""Server-owned environment label on every app-generated HTTP response."""

import re
import time

from fastapi import FastAPI
from starlette.types import Message, Receive, Scope, Send


class EnvironmentFastAPI(FastAPI):
    """Wrap the complete ASGI stack, including ServerErrorMiddleware's 500.

    This label supports T36 request attribution; it is not an authenticated
    cloud identity. The registered route/producer must be verified separately.
    """

    def __init__(self, *, environment: str, **kwargs):
        if not isinstance(environment, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", environment
        ):
            raise ValueError("APP_ENV must be a bounded ASCII environment identifier")
        self._environment_header = environment.encode("ascii")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def send_with_environment(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-bravo-env"
                ]
                headers.append((b"x-bravo-env", self._environment_header))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await super().__call__(scope, receive, send_with_environment)
        finally:
            metrics = getattr(self.state, "http_metrics", None)
            if metrics is not None:
                try:
                    metrics.record(scope, status, time.perf_counter() - started)
                except Exception:
                    # Observability cannot fail or replace the business response.
                    pass

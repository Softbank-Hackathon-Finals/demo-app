"""Startup policy: reject new uploads before consuming multipart bodies.

This instance-local policy is not a cluster-wide drain receipt. The migration
orchestrator must retire old writers and verify every serving replica separately.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class AttachmentUploadPolicy:
    def __init__(self, app: ASGIApp, *, enabled: bool):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            not self.enabled
            and scope["type"] == "http"
            and scope.get("method") == "PUT"
            and scope.get("path", "").startswith("/api/attachments/")
        ):
            response = JSONResponse(
                {"detail": "attachment uploads are disabled for migration"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

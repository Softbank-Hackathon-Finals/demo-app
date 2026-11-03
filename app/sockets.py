"""Serialize each connection's sends with the cutover close frame."""

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect


class CloseUnconfirmed(Exception):
    """A close was requested but the transport has not confirmed completion."""


@dataclass(eq=False)
class SocketSession:
    socket: WebSocket
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_task: asyncio.Task | None = None

    async def send_json(self, value) -> None:
        async with self.lock:
            if not self.closed and self.close_task is None:
                await self.socket.send_json(value)

    async def close_for_cutover(self) -> bool:
        if self.closed:
            return False
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close_once())
            # Observe a late error even if every HTTP waiter timed out. Keep the task
            # and its result so a retry observes the same operation, never sends again.
            self.close_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
        if self.close_task.cancelled():
            raise CloseUnconfirmed("close operation was cancelled")
        # The request deadline cancels the waiter, not Starlette's in-flight send.
        return await asyncio.shield(self.close_task)

    async def _close_once(self) -> bool:
        async with self.lock:
            if self.closed:
                return False
            try:
                await self.socket.close(code=1012, reason="cutover")
            except WebSocketDisconnect:
                # The transport already confirmed this client disconnected.
                self.closed = True
                return False
            except Exception as error:
                raise CloseUnconfirmed("close transport did not confirm completion") from error
            self.closed = True
            return True

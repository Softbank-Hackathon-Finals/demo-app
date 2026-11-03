"""Serialize each connection's sends with the cutover close frame."""

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect


@dataclass(eq=False)
class SocketSession:
    socket: WebSocket
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, value) -> None:
        async with self.lock:
            if not self.closed:
                await self.socket.send_json(value)

    async def close_for_cutover(self) -> bool:
        async with self.lock:
            if self.closed:
                return False
            try:
                await self.socket.close(code=1012, reason="cutover")
            except WebSocketDisconnect:
                # The transport already confirmed this client disconnected.
                self.closed = True
                return False
            self.closed = True
            return True

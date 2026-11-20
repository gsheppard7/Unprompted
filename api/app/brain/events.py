"""In-process listeners for Brain UI refreshes."""

from __future__ import annotations

from fastapi import WebSocket


class BrainHub:
    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    def join(self, socket: WebSocket) -> None:
        self.sockets.add(socket)

    def leave(self, socket: WebSocket) -> None:
        self.sockets.discard(socket)

    async def broadcast(self, message: dict) -> None:
        for socket in list(self.sockets):
            try:
                await socket.send_json(message)
            except Exception:
                self.leave(socket)


hub = BrainHub()

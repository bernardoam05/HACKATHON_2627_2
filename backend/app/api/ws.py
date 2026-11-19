"""WebSocket broadcaster. (Shared contract)"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter()


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, msg: BaseModel) -> None:
        text = msg.model_dump_json()
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(ws)


broadcaster = Broadcaster()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()  # clients send nothing; keeps connection open
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)

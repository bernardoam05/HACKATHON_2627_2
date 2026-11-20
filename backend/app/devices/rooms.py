"""Room store (rooms are ours; Altice returns none).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import unicodedata
import uuid

from app.schemas import Room, RoomCreate
from app.storage.repository import Repository


class RoomStore:
    """CRUD for Room; implements RoomService."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._lock = asyncio.Lock()

    async def list_rooms(self) -> list[Room]:
        return await self._repo.list_rooms()

    async def get_room(self, room_id: str) -> Room | None:
        return next((r for r in await self._repo.list_rooms() if r.id == room_id), None)

    async def create_room(self, body: RoomCreate) -> Room:
        room = Room(id=uuid.uuid4().hex[:12], name=body.name, isEntrance=body.isEntrance)
        await self._repo.save_room(room)
        return room

    async def delete_room(self, room_id: str) -> None:
        """Deleting a room unassigns its devices. Unknown ids are a no-op (idempotent DELETE)."""
        await self._repo.delete_room(room_id)

    async def ensure_room(self, name: str) -> Room:
        """The room with this name (case/accents ignored), created if missing. A room called
        "Entrada"/"Hall" is marked as the entrance (D14 needs one); the user can change it."""
        async with self._lock:
            key = _norm(name)
            for room in await self._repo.list_rooms():
                if _norm(room.name) == key:
                    return room
            entrance = any(w in key for w in ("entrada", "hall", "entrance"))
            return await self.create_room(RoomCreate(name=name.strip(), isEntrance=entrance))


def _norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())

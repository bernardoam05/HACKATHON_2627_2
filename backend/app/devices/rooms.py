"""Room store (rooms are ours; Altice returns none).

# Owner: Team A
"""
from __future__ import annotations

import uuid

from app.schemas import Room, RoomCreate
from app.storage.repository import Repository


class RoomStore:
    """CRUD for Room; implements RoomService."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

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

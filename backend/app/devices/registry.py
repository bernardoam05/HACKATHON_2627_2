"""Merged device registry over Altice + sim sources.

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.mcp_clients.base import McpClient
from app.mcp_clients.sim import SimClient
from app.schemas import (
    CapabilityUpdate,
    Device,
    DeviceAssignment,
    DeviceSource,
    SimDeviceCreate,
)
from app.storage.repository import Repository

from .rooms import RoomStore

log = logging.getLogger(__name__)


class DeviceRegistry:
    """Merges devices from both clients, applies overrides (alias/room/enabled), read_state() and set_capability(). Implements DeviceService and SimAdminService.

    The poller calls `read_source()`; the API reads the last known state (`list_devices`) so it
    never blocks on a slow Altice call. Writes go through `write()`, used by the executor and by
    the manual-control route.
    """

    def __init__(
        self,
        repo: Repository,
        rooms: RoomStore,
        clients: dict[DeviceSource, McpClient],
    ) -> None:
        self._repo = repo
        self._rooms = rooms
        self._clients = clients
        self._cache: dict[DeviceSource, list[Device]] = {}
        # Set by the wiring to the executor's manual path (validation + override feedback).
        self.manual_handler: Callable[[str, CapabilityUpdate], Awaitable[Device]] | None = None

    # --- reading ----------------------------------------------------------------------------

    @property
    def sources(self) -> list[DeviceSource]:
        return list(self._clients)

    async def read_source(self, source: DeviceSource) -> list[Device]:
        """Fetch a source, apply overrides, refresh the cache. Raises if the source is down."""
        devices = await self._clients[source].list_devices()
        devices = await self._apply_overrides(devices)
        self._cache[source] = devices
        return devices

    async def read_state(self) -> list[Device]:
        """Fresh read of every source; a failing source keeps its last known devices."""
        results = await asyncio.gather(
            *(self.read_source(s) for s in self._clients), return_exceptions=True
        )
        for source, res in zip(self._clients, results, strict=True):
            if isinstance(res, BaseException):
                log.warning("read_state: %s unavailable: %r", source.value, res)
        return self.cached()

    def cached(self) -> list[Device]:
        return [d for s in self._clients for d in self._cache.get(s, [])]

    async def list_devices(self) -> list[Device]:
        if not self._cache:
            return await self.read_state()
        # Re-apply overrides so manager edits show up before the next poll.
        return await self._apply_overrides(self.cached())

    async def get_device(self, device_id: str) -> Device:
        for d in await self.list_devices():
            if d.id == device_id:
                return d
        raise KeyError(device_id)

    async def _apply_overrides(self, devices: list[Device]) -> list[Device]:
        overrides = await self._repo.get_overrides()
        out: list[Device] = []
        for d in devices:
            o = overrides.get(d.id)
            if o:
                d = d.model_copy(
                    update={
                        "alias": o["alias"],
                        "roomId": o["roomId"],
                        "enabled": True if o["enabled"] is None else o["enabled"],
                    }
                )
            out.append(d)
        return out

    # --- DeviceService ----------------------------------------------------------------------

    async def assign(self, device_id: str, body: DeviceAssignment) -> Device:
        await self.get_device(device_id)  # KeyError if unknown
        fields = body.model_dump(exclude_unset=True)
        if fields.get("roomId") and await self._rooms.get_room(fields["roomId"]) is None:
            raise KeyError(fields["roomId"])
        await self._repo.set_override(device_id, **fields)
        return await self.get_device(device_id)

    async def set_capability(self, device_id: str, update: CapabilityUpdate) -> Device:
        """Manual control from the UI: goes through the executor's manual path when wired."""
        if self.manual_handler is not None:
            return await self.manual_handler(device_id, update)
        return await self.write(device_id, update)

    async def write(self, device_id: str, update: CapabilityUpdate) -> Device:
        """The single write path to devices (executor + manual control)."""
        device = await self.get_device(device_id)
        client = self._clients[device.source]
        if update.type == "securityStatus":  # alarm: not a device capability on Altice
            set_status = getattr(client, "set_security_status", None)
            if set_status is None:
                raise NotImplementedError(f"{device.source.value} has no security status tool")
            await set_status(str(update.value))
            return device
        updated = await client.update_capabilities(device_id, update)
        if updated is None:
            updated = await self._clients[device.source].get_device(device_id)
        [merged] = await self._apply_overrides([updated])
        self._replace_cached(merged)
        return merged

    def _replace_cached(self, device: Device) -> None:
        devices = self._cache.get(device.source, [])
        self._cache[device.source] = [device if d.id == device.id else d for d in devices]

    # --- SimAdminService --------------------------------------------------------------------

    def _sim(self) -> SimClient:
        client = self._clients.get(DeviceSource.sim)
        if not isinstance(client, SimClient):
            raise NotImplementedError("Simulator source is not configured")
        return client

    async def list_templates(self) -> list[dict[str, str]]:
        return await self._sim().list_templates()

    async def add_device(self, body: SimDeviceCreate) -> Device:
        room_name = None
        if body.roomId:
            room = await self._rooms.get_room(body.roomId)
            if room is None:
                raise KeyError(body.roomId)
            room_name = room.name
        caps = [c.model_dump(exclude_none=True) for c in body.capabilities or []] or None
        device = await self._sim().add_device(body.template or body.type, body.name, room_name, caps)
        if body.roomId:
            await self._repo.set_override(device.id, roomId=body.roomId)
        [device] = await self._apply_overrides([device])
        self._cache.setdefault(DeviceSource.sim, []).append(device)
        return device

    async def remove_device(self, device_id: str) -> None:
        device = await self.get_device(device_id)
        if device.source == DeviceSource.altice:
            raise ValueError("Altice devices cannot be removed")
        await self._sim().remove_device(device_id)
        self._cache[DeviceSource.sim] = [
            d for d in self._cache.get(DeviceSource.sim, []) if d.id != device_id
        ]

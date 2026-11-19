"""Simulator MCP client (our server, same tool names as Altice, plus sim_* admin tools).

# Owner: Team A
"""
from __future__ import annotations

from typing import Any

from app.schemas import Device, DeviceSource

from .base import McpClient


class SimClient(McpClient):
    """Implements McpClient against SIM_MCP_URL; adds sim_list_templates/add_device/remove_device."""

    source = DeviceSource.sim

    def __init__(self, url: Any, timeout_seconds: float = 5.0) -> None:
        super().__init__(url, timeout_seconds=timeout_seconds)

    async def list_templates(self) -> list[dict[str, str]]:
        data = await self.call("sim_list_templates")
        return list(data or [])

    async def add_device(
        self,
        template: str,
        name: str,
        room_name: str | None = None,
        capabilities: list[dict[str, Any]] | None = None,
    ) -> Device:
        args: dict[str, Any] = {"template": template, "name": name}
        if room_name is not None:
            args["roomName"] = room_name
        if capabilities is not None:
            args["capabilities"] = capabilities
        return self._device(await self.call("sim_add_device", args))

    async def remove_device(self, device_id: str) -> None:
        await self.call("sim_remove_device", {"deviceId": device_id})

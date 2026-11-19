"""Altice MCP client (real house). Only the executor may call its write tools.

# Owner: Team A
"""
from __future__ import annotations

from typing import Any

from app.schemas import DeviceSource

from .base import McpClient


class AlticeClient(McpClient):
    """Implements McpClient against ALTICE_MCP_URL with ALTICE_MCP_TOKEN. Tolerates latency/failures."""

    source = DeviceSource.altice

    def __init__(self, url: Any, token: str = "", timeout_seconds: float = 15.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        super().__init__(url, headers=headers, timeout_seconds=timeout_seconds)

    async def security_status(self) -> Any:
        return await self.call("get_home_security_status")

    async def set_security_status(self, status: str, delay_seconds: int = 0) -> Any:
        """Write tool: arm/disarm the alarm (`disarm` | `stay` | `away`). Executor only, after confirmation."""
        delays = [{"status": s, "value": delay_seconds} for s in ("STAY", "AWAY")]
        body = {"status": status, "alarmDelay": delays, "armDelay": delays}
        return await self.call("patch_home_security_status", {"body": body})

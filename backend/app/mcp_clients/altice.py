"""Altice MCP client (real house). Only the executor may call its write tools.

Authentication needs both:
- `X-API-Key: <ALTICE_MCP_TOKEN>` + `X-Client-Type: <ALTICE_CLIENT_TYPE>` (headers), and
- `Authorization: Bearer <Keycloak access token>`, handled by the OAuth provider in `auth.py`
  (log in once with `python -m app.mcp_clients.auth login`).

# Owner: Team A
"""
from __future__ import annotations

from typing import Any

import httpx2

from app.schemas import DeviceSource

from .base import McpClient


class AlticeClient(McpClient):
    """Implements McpClient against ALTICE_MCP_URL (API key headers + OAuth Bearer). Tolerates latency/failures."""

    source = DeviceSource.altice

    def __init__(
        self,
        url: Any,
        api_key: str = "",
        client_type: str = "core",
        auth: httpx2.Auth | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        headers = {"X-API-Key": api_key, "X-Client-Type": client_type} if api_key else None
        super().__init__(url, headers=headers, auth=auth, timeout_seconds=timeout_seconds)

    async def security_status(self) -> Any:
        return await self.call("get_home_security_status")

    async def set_security_status(self, status: str, delay_seconds: int = 0) -> Any:
        """Write tool: arm/disarm the alarm (`disarm` | `stay` | `away`). Executor only, after confirmation."""
        delays = [{"status": s, "value": delay_seconds} for s in ("STAY", "AWAY")]
        body = {"status": status, "alarmDelay": delays, "armDelay": delays}
        return await self.call("patch_home_security_status", {"body": body})

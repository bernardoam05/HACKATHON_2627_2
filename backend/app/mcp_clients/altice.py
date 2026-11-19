"""Altice MCP client (real house). Only the executor may call its write tools.

# Owner: Team A
"""
from __future__ import annotations


class AlticeClient:
    """Implements McpClient against ALTICE_MCP_URL with ALTICE_MCP_TOKEN. Tolerates latency/failures."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class AlticeClient: Implements McpClient against ALTICE_MCP_URL with ALTICE_MCP_TOKEN. Tolerates latency/failures.")

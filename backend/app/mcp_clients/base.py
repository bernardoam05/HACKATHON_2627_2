"""MCP client base: async session wrapper with reconnect and timeouts.

# Owner: Team A
"""
from __future__ import annotations


class McpClient:
    """Wraps an MCP session; methods: list_devices(), get_device(id), update_capabilities(id, CapabilityUpdate), rooms()."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class McpClient: Wraps an MCP session; methods: list_devices(), get_device(id), update_capabilities(id, CapabilityUpdate), rooms().")

"""Simulator MCP client (our server, same tool names as Altice, plus sim_* admin tools).

# Owner: Team A
"""
from __future__ import annotations


class SimClient:
    """Implements McpClient against SIM_MCP_URL; adds sim_list_templates/add_device/remove_device."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class SimClient: Implements McpClient against SIM_MCP_URL; adds sim_list_templates/add_device/remove_device.")

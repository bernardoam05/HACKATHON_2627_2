"""Simulator MCP server: same tool names and payloads as the Altice MCP (PLAN 5.7).

# Owner: Team B
Run: cd simulator && uv run python server.py   (port 8100)
Tools are registered with stub bodies; Team B implements behaviour.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("home-simulator", port=8100)


# --- Altice-compatible tools -------------------------------------------------
@mcp.tool()
def get_home_devices() -> dict[str, Any]:
    """Return {"devices": Device[]} (Altice shape, without our extra fields)."""
    raise NotImplementedError("Team B: list simulated devices")


@mcp.tool()
def get_device_details(deviceId: str) -> dict[str, Any]:
    """Return one Device."""
    raise NotImplementedError("Team B: return device details")


@mcp.tool()
def update_device_capabilities(deviceId: str, body: dict[str, Any]) -> dict[str, Any]:
    """Apply a CapabilityUpdate (power / brightness / ...) and return the updated Device.
    Brightness writes may fade over a short time."""
    raise NotImplementedError("Team B: apply capability update")


@mcp.tool()
def get_home_rooms() -> dict[str, Any]:
    """Return {"rooms": []} like Altice (rooms are defined by our backend)."""
    return {"rooms": []}


# --- Simulator-only admin tools (never used by the agent) --------------------
@mcp.tool()
def sim_list_templates() -> list[dict[str, str]]:
    """List templates: tv, coffee_plug, bulb, contactSensor, motionSensor."""
    raise NotImplementedError("Team B: list templates from simulator/templates/")


@mcp.tool()
def sim_add_device(template: str, name: str, roomName: str | None = None,
                   capabilities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create a simulated device from a template."""
    raise NotImplementedError("Team B: add device")


@mcp.tool()
def sim_remove_device(deviceId: str) -> dict[str, Any]:
    """Remove a simulated device."""
    raise NotImplementedError("Team B: remove device")


@mcp.tool()
def sim_set_state(deviceId: str, capability: str, value: Any) -> dict[str, Any]:
    """Simulate a PHYSICAL event (remote pressed, walking into the corridor)."""
    raise NotImplementedError("Team B: set state as a physical event")


@mcp.tool()
def sim_load_scenario(name: str) -> dict[str, Any]:
    """Put the world in the start state of a demo scenario (s1..s5)."""
    raise NotImplementedError("Team B: load scenario")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

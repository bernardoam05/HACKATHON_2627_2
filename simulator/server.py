"""Simulator MCP server: same tool names and payloads as the Altice MCP (PLAN 5.7).

# Owner: Team B
Run: cd simulator && uv run python server.py   (http://localhost:8100/mcp, streamable HTTP)

Env: SIM_HOST (127.0.0.1), SIM_PORT (8100), SIM_COFFEE_HEAT_SECONDS (120),
SIM_MOTION_CLEAR_SECONDS (30; 0 = motion never clears by itself).

Every tool returns a JSON object (one text block + structuredContent). Refused requests
raise ToolError, so the client gets isError=True with the reason.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from world import SimError, World

world = World(
    coffee_heat_seconds=float(os.getenv("SIM_COFFEE_HEAT_SECONDS", "120")),
    motion_clear_seconds=float(os.getenv("SIM_MOTION_CLEAR_SECONDS", "30")),
)
mcp = MCPServer("home-simulator")


@contextmanager
def _refusals() -> Iterator[None]:
    try:
        yield
    except SimError as exc:
        raise ToolError(str(exc)) from exc


# --- Altice-compatible tools -------------------------------------------------
@mcp.tool()
def get_home_devices() -> dict[str, Any]:
    """Retrieve all devices from the user home: {"devices": Device[]} (Altice shape)."""
    return {"devices": world.list_devices()}


@mcp.tool()
def get_device_details(deviceId: str, techInfo: bool = False, vendor: str | None = None) -> dict[str, Any]:
    """Retrieve device details (Altice shape, includes pairedTime)."""
    with _refusals():
        return world.get_device(deviceId)


@mcp.tool()
def update_device_capabilities(
    body: dict[str, Any],
    deviceId: str | None = None,
    deviceName: str | None = None,
    vendor: str | None = None,
) -> dict[str, Any]:
    """Update one capability of a device by identifier or name; returns the updated Device.

    body: {"type": "power", "value": "on"|"off"} | {"type": "brightness"|"temperature"|"volume",
    "value": number} | {"type": "color_rgb", "redValue", "greenValue", "blueValue"}.
    Sensor readings are not writable.
    """
    with _refusals():
        return world.update_capabilities(body, device_id=deviceId, device_name=deviceName)


@mcp.tool()
def get_home_rooms() -> dict[str, Any]:
    """Return {"rooms": []} like Altice (rooms are defined by our backend)."""
    return {"rooms": []}


# --- Simulator-only admin tools (never used by the agent) --------------------
@mcp.tool()
def sim_list_templates() -> dict[str, Any]:
    """List device templates: {"templates": [{id, type, name}]}."""
    return {"templates": world.list_templates()}


@mcp.tool()
def sim_add_device(
    template: str,
    name: str,
    roomName: str | None = None,
    capabilities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a simulated device from a template; `capabilities` overrides/extends by type."""
    with _refusals():
        return world.add_device(template, name, roomName, capabilities)


@mcp.tool()
def sim_remove_device(deviceId: str) -> dict[str, Any]:
    """Remove a simulated device: {"deviceId", "removed": true}."""
    with _refusals():
        return world.remove_device(deviceId)


@mcp.tool()
def sim_set_state(deviceId: str, capability: str, value: Any) -> dict[str, Any]:
    """Simulate a PHYSICAL event (remote pressed, walking into the corridor, door opened).

    Lenient values: power true/"on"/"ligado"; contact sensorState "open"/"closed";
    motionDetected true/false. Returns the updated Device.
    """
    with _refusals():
        return world.set_state(deviceId, capability, value)


@mcp.tool()
def sim_load_scenario(name: str) -> dict[str, Any]:
    """Put the world in the start state of demo/scenarios/<name>.yaml (s1..s5)."""
    with _refusals():
        return world.load_scenario(name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mcp.run(
        transport="streamable-http",
        host=os.getenv("SIM_HOST", "127.0.0.1"),
        port=int(os.getenv("SIM_PORT", "8100")),
    )

"""Team A: MCP clients, merged registry and poller against in-process fake MCP servers."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from app.devices.registry import DeviceRegistry
from app.devices.rooms import RoomStore
from app.mcp_clients.altice import AlticeClient
from app.mcp_clients.base import McpUnavailable
from app.mcp_clients.sim import SimClient
from app.poller.diff import DEVICE_PRESENCE
from app.poller.poller import Poller
from app.schemas import (
    ActionRecord,
    ActionStatus,
    AutonomyLevel,
    CapabilityUpdate,
    DeviceAssignment,
    DeviceSource,
    RoomCreate,
    SimDeviceCreate,
)
from app.storage.db import init_db
from app.storage.repository import Repository

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ALTICE_BULB = "dc9814bc-81bb-4546-b22d-80705b65fd22"


def fake_server(devices: list[dict[str, Any]], with_sim_tools: bool = False) -> MCPServer:
    """Minimal Altice-shaped server holding state in memory. Never touches the real house."""
    state = {d["id"]: d for d in copy.deepcopy(devices)}
    server = MCPServer("fake")
    server.state = state  # type: ignore[attr-defined]

    @server.tool()
    def get_home_devices() -> dict[str, Any]:
        return {"devices": list(state.values())}

    @server.tool()
    def get_device_details(deviceId: str) -> dict[str, Any]:
        return state[deviceId]

    @server.tool()
    def update_device_capabilities(deviceId: str, body: dict[str, Any]) -> dict[str, Any]:
        for cap in state[deviceId]["capabilities"]:
            if cap["type"] == body["type"]:
                cap["value"] = body["value"]
        return state[deviceId]

    if with_sim_tools:

        @server.tool()
        def sim_list_templates() -> list[dict[str, str]]:
            return [{"id": "tv", "type": "tv", "name": "TV"}]

        @server.tool()
        def sim_add_device(
            template: str, name: str, roomName: str | None = None,
            capabilities: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            dev = {"id": f"sim-{len(state)}", "name": name, "type": template,
                   "capabilities": [{"type": "power", "value": "off"}], "roomName": roomName}
            state[dev["id"]] = dev
            return dev

        @server.tool()
        def sim_remove_device(deviceId: str) -> dict[str, Any]:
            state.pop(deviceId)
            return {"ok": True}

    return server


def sim_devices() -> list[dict[str, Any]]:
    return [
        {"id": "tv1", "name": "TV", "type": "tv",
         "capabilities": [{"type": "power", "value": "off"}, {"type": "volume", "value": 10}]},
        {"id": "lamp1", "name": "Candeeiro", "type": "bulb",
         "capabilities": [{"type": "power", "value": "on"},
                          {"type": "brightness", "value": 100, "min": 1, "max": 100}]},
    ]


class Recorder:
    def __init__(self) -> None:
        self.msgs: list[BaseModel] = []
        self.runs = 0

    async def broadcast(self, msg: BaseModel) -> None:
        self.msgs.append(msg)

    async def request_run(self) -> None:
        self.runs += 1


@pytest.fixture
async def world():
    repo = Repository(await init_db(":memory:"))
    altice_devices = json.loads((FIXTURES / "get_home_devices.json").read_text())["devices"]
    altice_srv, sim_srv = fake_server(altice_devices), fake_server(sim_devices(), True)
    clients = {DeviceSource.altice: AlticeClient(altice_srv), DeviceSource.sim: SimClient(sim_srv)}
    rooms = RoomStore(repo)
    registry = DeviceRegistry(repo, rooms, clients)
    rec = Recorder()
    poller = Poller(registry, repo, rec, rec.broadcast, {})  # type: ignore[arg-type]
    yield {"repo": repo, "registry": registry, "rooms": rooms, "poller": poller, "rec": rec,
           "sim_srv": sim_srv}
    for c in clients.values():
        await c.close()
    await repo.close()


async def test_registry_merges_sources_and_overrides(world):
    reg: DeviceRegistry = world["registry"]
    devices = await reg.read_state()
    assert len(devices) == 9
    assert {d.source for d in devices} == {DeviceSource.altice, DeviceSource.sim}

    room = await world["rooms"].create_room(RoomCreate(name="Sala"))
    dev = await reg.assign("tv1", DeviceAssignment(alias="TV da sala", roomId=room.id))
    assert (dev.alias, dev.roomId, dev.enabled) == ("TV da sala", room.id, True)
    dev = await reg.assign("tv1", DeviceAssignment(enabled=False))
    assert dev.alias == "TV da sala" and dev.enabled is False
    with pytest.raises(KeyError):
        await reg.assign("tv1", DeviceAssignment(roomId="nope"))


async def test_registry_write_and_sim_admin(world):
    reg: DeviceRegistry = world["registry"]
    await reg.read_state()
    dev = await reg.write("lamp1", CapabilityUpdate(type="brightness", value=30))
    assert dev.capability("brightness").value == 30

    assert await reg.list_templates() == [{"id": "tv", "type": "tv", "name": "TV"}]
    added = await reg.add_device(SimDeviceCreate(type="motionSensor", name="Corredor"))
    assert added.source == DeviceSource.sim and added.type == "motionSensor"
    await reg.remove_device(added.id)
    assert all(d.id != added.id for d in await reg.list_devices())
    with pytest.raises(ValueError):
        await reg.remove_device(ALTICE_BULB)


async def test_unreachable_source_raises_unavailable():
    client = SimClient("http://127.0.0.1:9/mcp")
    client._connect_timeout = 1.0
    with pytest.raises(McpUnavailable):
        await client.list_devices()
    await client.close()


async def test_poller_baseline_then_change_wakes_agent(world):
    poller: Poller = world["poller"]
    rec: Recorder = world["rec"]
    assert await poller.poll_once(DeviceSource.sim) == []  # baseline

    world["sim_srv"].state["tv1"]["capabilities"][0]["value"] = "on"  # user presses the remote
    changes = await poller.poll_once(DeviceSource.sim)
    assert [(c.deviceId, c.capability, c.old, c.new) for c in changes] == [("tv1", "power", "off", "on")]
    assert rec.runs == 1
    assert [m.type for m in rec.msgs] == ["state_changed"]
    stored = await world["repo"].list_changes("tv1", None, 10)
    assert stored[0].new == "on" and not stored[0].causedByAgent


async def test_poller_agent_change_does_not_wake_agent(world):
    poller: Poller = world["poller"]
    rec: Recorder = world["rec"]
    repo: Repository = world["repo"]
    await poller.poll_once(DeviceSource.sim)
    await repo.save_action(
        ActionRecord(
            id="act1", decisionId="d1", deviceId="lamp1",
            update=CapabilityUpdate(type="brightness", value=30), autonomy=AutonomyLevel.auto,
            status=ActionStatus.executed, expectedValue=30, createdAt=datetime.now(UTC),
        )
    )
    await world["registry"].write("lamp1", CapabilityUpdate(type="brightness", value=30))
    [c] = await poller.poll_once(DeviceSource.sim)
    assert c.causedByAgent and c.actionId == "act1"
    assert rec.runs == 0


async def test_poller_device_added_and_disabled(world):
    poller: Poller = world["poller"]
    rec: Recorder = world["rec"]
    reg: DeviceRegistry = world["registry"]
    await poller.poll_once(DeviceSource.sim)
    await reg.add_device(SimDeviceCreate(type="motionSensor", name="Corredor"))
    changes = await poller.poll_once(DeviceSource.sim)
    assert [c.capability for c in changes] == [DEVICE_PRESENCE]
    assert "device_added" in [m.type for m in rec.msgs]

    await reg.assign("tv1", DeviceAssignment(enabled=False))
    world["sim_srv"].state["tv1"]["capabilities"][1]["value"] = 50
    assert await poller.poll_once(DeviceSource.sim) == []  # disabled devices are ignored


async def test_poller_failing_source_does_not_raise(world):
    poller: Poller = world["poller"]
    poller._registry._clients[DeviceSource.altice] = SimClient("http://127.0.0.1:9/mcp")
    poller._registry._clients[DeviceSource.altice]._connect_timeout = 0.5
    assert await poller.poll_once(DeviceSource.altice) == []
    await poller._registry._clients[DeviceSource.altice].close()

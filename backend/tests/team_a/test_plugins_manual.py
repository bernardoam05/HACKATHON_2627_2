"""Team A: builtin plugins match real Altice capability names; manual control from the UI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server

from app.agent.executor import InvalidAction
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.plugins.defaults import load_builtin_plugins
from app.schemas import CapabilityUpdate, DeviceSource, ProposedAction

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PSEUDO_CAPABILITIES = {("gateway", "securityStatus")}  # written via patch_home_security_status


def test_builtin_plugins_use_real_altice_capability_names():
    devices = json.loads((FIXTURES / "get_home_devices.json").read_text())["devices"]
    real = {d["type"]: {c["type"] for c in d["capabilities"]} for d in devices}
    checked = 0
    for plugin in load_builtin_plugins():
        if plugin.type not in real:  # e.g. tv only exists in the simulator
            continue
        for spec in plugin.capabilities:
            if (plugin.type, spec.type) in PSEUDO_CAPABILITIES:
                continue
            assert spec.type in real[plugin.type], f"{plugin.type}.{spec.type} not on Altice device"
            checked += 1
    assert checked >= 20


def test_only_altice_writable_capabilities_are_writable():
    # update_device_capabilities only supports power / brightness / temperature / color_rgb
    allowed = {"power", "brightness", "temperature", "color_rgb", "volume"}
    for plugin in load_builtin_plugins():
        for spec in plugin.capabilities:
            if spec.writable and (plugin.type, spec.type) not in PSEUDO_CAPABILITIES:
                assert spec.type in allowed, f"{plugin.type}.{spec.type} is not writable via MCP"


def noop_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    out = {"explanation": "Nada a fazer.", "beliefState": "b", "actions": []}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


@pytest.fixture
async def home():
    srv = fake_server([
        {"id": "lamp", "name": "Candeeiro", "type": "bulb",
         "capabilities": [{"type": "power", "value": "on", "availableValues": ["on", "off"]},
                          {"type": "brightness", "value": 100, "min": 1, "max": 100}]},
        {"id": "plug", "name": "Ficha", "type": "plug", "categories": ["energy"],
         "capabilities": [{"type": "power", "value": "on"}, {"type": "instantPower", "value": 5}]},
    ], with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="")
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(noop_model)
    )
    services.scheduler._min_gap = 0
    await services.poller.poll_once(DeviceSource.sim)
    yield services, srv
    await services.aclose()


async def test_manual_control_overriding_agent_is_recorded_at_once(home):
    services, srv = home
    executor = services.agent._executor
    await executor.apply("d1", ProposedAction(deviceId="lamp",
                                              update=CapabilityUpdate(type="brightness", value=30)))

    # The user turns it back up from the app before the poller even saw the agent's change
    dev = await services.devices.set_capability("lamp", CapabilityUpdate(type="brightness", value=90))
    assert dev.capability("brightness").value == 90
    [fb] = await services.repo.recent_feedback(5)
    assert fb.kind == "manual_override" and "via app" in (fb.note or "")
    assert services.feedback.in_cooldown("lamp")

    # The poller then sees a user change: not tagged, wakes the agent, no duplicate feedback
    [change] = await services.poller.poll_once(DeviceSource.sim)
    await services.scheduler.wait_idle()
    assert not change.causedByAgent and change.new == 90
    assert len(await services.repo.recent_feedback(5)) == 1


async def test_manual_control_is_validated(home):
    services, srv = home
    with pytest.raises(InvalidAction):  # ValueError -> 409
        await services.devices.set_capability("plug", CapabilityUpdate(type="instantPower", value=0))
    dev = await services.devices.set_capability("lamp", CapabilityUpdate(type="brightness", value=0))
    assert dev.capability("brightness").value == 1  # clamped to min
    # Manual control is a user action: never stored as an agent ActionRecord, no feedback
    assert await services.actions.list_actions(None, 10) == []
    assert await services.repo.recent_feedback(5) == []

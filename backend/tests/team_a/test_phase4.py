"""Team A phase 4: unknown-device onboarding (S5, D12)."""
from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server

from app.agent.onboarding import CapabilityDraft, Onboarding, PluginDraft, finalize
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import (
    AutonomyLevel,
    ConfigMode,
    Device,
    DevicePurpose,
    DeviceSource,
    PluginStatus,
    SimDeviceCreate,
)

MOTION = Device.model_validate({
    "id": "m1", "name": "Sensor corredor", "type": "motionSensor", "source": "sim",
    "capabilities": [{"type": "motionDetected", "value": False},
                     {"type": "lastMotionAt", "value": None},
                     {"type": "sirenSwitch", "value": False}],
})

DRAFT = {
    "displayName": "Sensor de movimento",
    "purpose": "comfort",
    "capabilities": [
        {"type": "motionDetected", "description": "Movimento detetado", "writable": False,
         "autonomy": "auto"},
        {"type": "sirenSwitch", "description": "Sirene", "writable": True, "autonomy": "auto"},
        {"type": "teleport", "description": "not on the device", "writable": True,
         "autonomy": "auto"},
    ],
    "starterAutomations": ["Acender a luz do corredor quando há movimento"],
    "rationale": "Deteta presença numa divisão.",
}


def onboarding_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Answers onboarding requests with DRAFT and house-agent runs with a no-op decision."""
    names = {t.name for t in info.output_tools}
    schema = json.dumps([t.parameters_json_schema for t in info.output_tools])
    out: dict[str, Any] = DRAFT if "starterAutomations" in schema else {
        "explanation": "Nada a fazer.", "beliefState": "b", "actions": []}
    return ModelResponse(parts=[ToolCallPart(next(iter(names)), json.dumps(out))])


def failing_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise RuntimeError("provider down")


def specs(plugin) -> dict[str, tuple[bool, AutonomyLevel]]:
    return {s.type: (s.writable, s.autonomy) for s in plugin.capabilities}


def test_finalize_enforces_device_capabilities_and_security():
    proposal = finalize(MOTION, PluginDraft.model_validate(DRAFT))
    p = proposal.plugin
    assert p.status == PluginStatus.proposed and p.configMode == ConfigMode.basic
    assert specs(p) == {
        "motionDetected": (False, AutonomyLevel.auto),
        "lastMotionAt": (False, AutonomyLevel.suggest),  # missing in draft -> conservative
        "sirenSwitch": (True, AutonomyLevel.suggest),  # security is always suggest
    }
    assert proposal.rationale == "Deteta presença numa divisão."


def test_finalize_security_device_writable_caps_are_suggest():
    lock = Device(id="l", type="smartLock", categories=["security"],
                  capabilities=[{"type": "bolt", "value": "closed"}])
    draft = PluginDraft(displayName="Fechadura", purpose=DevicePurpose.security,
                        capabilities=[CapabilityDraft(type="bolt", writable=True,
                                                      autonomy=AutonomyLevel.auto)])
    assert specs(finalize(lock, draft).plugin)["bolt"] == (True, AutonomyLevel.suggest)


async def test_onboarding_llm_and_fallback():
    ok = await Onboarding(FunctionModel(onboarding_model)).propose(MOTION)
    assert ok.plugin.displayName == "Sensor de movimento"
    fallback = await Onboarding(FunctionModel(failing_model), retries=0).propose(MOTION)
    assert fallback.plugin.status == PluginStatus.proposed
    assert all(s.autonomy == AutonomyLevel.suggest for s in fallback.plugin.capabilities)
    assert "confirmação" in fallback.rationale


class Recorder:
    def __init__(self) -> None:
        self.msgs: list[BaseModel] = []

    async def __call__(self, msg: BaseModel) -> None:
        self.msgs.append(msg)


@pytest.fixture
async def home():
    srv = fake_server([], with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="")
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(onboarding_model)
    )
    services.scheduler._min_gap = 0
    rec = Recorder()
    services.poller._broadcast = rec
    await services.poller.poll_once(DeviceSource.sim)  # baseline
    yield services, srv, rec
    await services.aclose()


async def test_s5_unknown_device_basic_mode(home):
    services, srv, rec = home
    added = await services.sim.add_device(SimDeviceCreate(type="motionSensor", name="Corredor"))
    srv.state[added.id]["capabilities"] = [{"type": "motionDetected", "value": False}]
    await services.poller.poll_once(DeviceSource.sim)
    await services.scheduler.wait_idle()
    assert ("plugin_needed", "motionSensor") in [
        (m.type, getattr(m, "deviceType", None)) for m in rec.msgs]
    assert await services.plugins.unconfigured_types() == ["motionSensor"]

    device = await services.devices.get_device(added.id)
    proposal = await services.plugins.propose("motionSensor")
    assert proposal.plugin.status == PluginStatus.proposed
    # A proposal is not a configuration: still suggest, still unconfigured (D12)
    assert await services.plugins.resolve_autonomy(device, "motionDetected") == AutonomyLevel.suggest
    assert await services.plugins.unconfigured_types() == ["motionSensor"]
    assert "motionSensor" in {p.type for p in await services.plugins.list_plugins()}

    confirmed = await services.plugins.confirm_plugin("motionSensor", proposal.plugin)
    assert confirmed.status == PluginStatus.confirmed
    assert await services.plugins.resolve_autonomy(device, "motionDetected") == AutonomyLevel.auto
    assert await services.plugins.unconfigured_types() == []

    # The agent now gets the confirmed plugin in its context
    run_input = await services.agent.build_input([], chat=True)
    assert "motionSensor" in {p.type for p in run_input.plugins}


async def test_s5_advanced_mode_user_edits(home):
    services, _, _ = home
    await services.sim.add_device(SimDeviceCreate(type="motionSensor", name="Corredor"))
    proposal = await services.plugins.propose("motionSensor")
    edited = proposal.plugin.model_copy(update={
        "displayName": "Sensor PIR", "configMode": ConfigMode.advanced,
        "capabilities": [s.model_copy(update={"autonomy": AutonomyLevel.auto_notify})
                         for s in proposal.plugin.capabilities],
    })
    confirmed = await services.plugins.confirm_plugin("motionSensor", edited)
    assert confirmed.displayName == "Sensor PIR" and confirmed.configMode == ConfigMode.advanced
    assert {s.autonomy for s in confirmed.capabilities} == {AutonomyLevel.auto_notify}
    with pytest.raises(KeyError):
        await services.plugins.propose("toaster")  # no device of that type -> 404

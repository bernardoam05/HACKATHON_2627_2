"""Team A: agent + executor end to end on S1, with a scripted model (no real LLM call)."""
from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server, sim_devices

from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import ActionStatus, ChatRequest, DeviceSource

PROMPTS: list[str] = []


def _prompt(messages: list[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Dims the lamp when the TV turns on; turns the TV off when asked in chat."""
    prompt = _prompt(messages)
    PROMPTS.append(prompt)
    out: dict[str, Any] = {"explanation": "Nada a fazer.", "beliefState": "quiet", "actions": []}
    if '"deviceId": "tv1", "capability": "power", "old": "off", "new": "on"' in prompt:
        out = {
            "explanation": "Baixei a luz da sala porque ligaste a televisão.",
            "inferredActivity": "watching TV",
            "beliefState": "Someone is watching TV in the living room.",
            "actions": [{"deviceId": "lamp1", "update": {"type": "brightness", "value": 30},
                         "reason": "TV on"}],
        }
    elif "desliga a tv" in prompt.lower():
        out = {
            "explanation": "Proponho desligar a televisão.",
            "beliefState": "User asked to turn off the TV.",
            "actions": [{"deviceId": "tv1", "update": {"type": "power", "value": "off"}}],
        }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


@pytest.fixture
async def home():
    PROMPTS.clear()
    srv = fake_server(sim_devices(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="", NOW_OVERRIDE=None)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(scripted)
    )
    services.scheduler._min_gap = 0
    yield services, srv
    await services.aclose()


def lamp_brightness(srv) -> int:
    return next(c["value"] for c in srv.state["lamp1"]["capabilities"] if c["type"] == "brightness")


async def test_s1_tv_on_dims_light(home):
    services, srv = home
    poller, scheduler = services.poller, services.scheduler

    await poller.poll_once(DeviceSource.sim)  # baseline
    srv.state["tv1"]["capabilities"][0]["value"] = "on"  # user presses the remote
    await poller.poll_once(DeviceSource.sim)
    await scheduler.wait_idle()

    assert lamp_brightness(srv) == 30
    [action] = await services.actions.list_actions(None, 10)
    assert action.status == ActionStatus.executed and action.autonomy.value == "auto"
    [decision] = await services.actions.list_decisions(10)
    assert decision.explanation.startswith("Baixei a luz")
    info = decision.runInfo  # telemetry of the run, persisted with the decision
    assert info is not None and info.durationMs >= 0 and info.modelRequests >= 1
    assert info.mode == "full" and info.tier == "fast"
    assert len(PROMPTS) == 1 and "Mode: full" in PROMPTS[0]  # first run is always full

    status = await services.agent.status()
    assert status["beliefState"].startswith("Someone is watching TV")

    # The lamp change caused by the agent is tagged and does not wake the agent (D8)
    [change] = await poller.poll_once(DeviceSource.sim)
    assert change.causedByAgent and change.actionId == action.id
    await scheduler.wait_idle()
    assert len(PROMPTS) == 1

    # Undo restores the previous value
    undone = await services.actions.undo(action.id)
    assert undone.status == ActionStatus.undone and lamp_brightness(srv) == 100


async def test_second_run_is_delta_with_belief(home):
    services, srv = home
    poller, scheduler = services.poller, services.scheduler
    await poller.poll_once(DeviceSource.sim)
    srv.state["tv1"]["capabilities"][1]["value"] = 20
    await poller.poll_once(DeviceSource.sim)
    await scheduler.wait_idle()
    srv.state["tv1"]["capabilities"][1]["value"] = 25
    await poller.poll_once(DeviceSource.sim)
    await scheduler.wait_idle()
    assert len(PROMPTS) == 2
    assert "Mode: delta" in PROMPTS[1] and "Full snapshot" not in PROMPTS[1]
    assert "quiet" in PROMPTS[1]  # previous belief state is fed back
    assert '"old": 20, "new": 25' in PROMPTS[1] and '"new": 20' not in PROMPTS[1]


async def test_chat_tv_power_is_suggest_until_confirmed(home):
    services, srv = home
    await services.poller.poll_once(DeviceSource.sim)
    srv.state["tv1"]["capabilities"][0]["value"] = "on"
    await services.poller.poll_once(DeviceSource.sim)
    await services.scheduler.wait_idle()

    resp = await services.agent.chat(ChatRequest(message="Desliga a TV"))
    assert resp.reply == "Proponho desligar a televisão."
    [pending] = await services.actions.list_actions("pending_confirmation", 10)
    assert srv.state["tv1"]["capabilities"][0]["value"] == "on"  # not executed (tv.power = suggest)

    confirmed = await services.actions.confirm(pending.id)
    assert confirmed.status == ActionStatus.executed
    assert srv.state["tv1"]["capabilities"][0]["value"] == "off"


async def test_invalid_actions_are_not_executed(home):
    services, srv = home
    from app.schemas import CapabilityUpdate as CU
    from app.schemas import Decision, DecisionTrigger, ProposedAction

    await services.devices.list_devices()
    d = Decision(
        id="d", createdAt="2026-11-19T20:00:00Z", trigger=DecisionTrigger.state_change,
        explanation="x",
        actions=[
            ProposedAction(deviceId="nope", update=CU(type="power", value="on")),
            ProposedAction(deviceId="lamp1", update=CU(type="flux", value=1)),
            ProposedAction(deviceId="lamp1", update=CU(type="brightness", value=-5)),
            ProposedAction(deviceId="lamp1", update=CU(type="power", value="on")),  # no-op
        ],
    )
    records = await services.agent._executor.execute(d)
    assert [r.status for r in records] == [ActionStatus.failed, ActionStatus.failed,
                                           ActionStatus.executed]
    assert records[2].update.value == 1  # clamped to min
    assert lamp_brightness(srv) == 1

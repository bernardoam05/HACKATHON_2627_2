"""Team A phase 2: insights, reflexes (S2), manual override, presence facts (S4), alarm."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
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
from test_devices_poller import fake_server

from app.agent.reflexes import is_triggered
from app.agent.tools import PresenceWatcher, compute_presence_facts
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.altice import AlticeClient
from app.mcp_clients.sim import SimClient
from app.schemas import (
    ActionStatus,
    ChatRequest,
    DeviceAssignment,
    DeviceSource,
    InsightStatus,
    InsightUpdate,
    Reflex,
    ReflexStatus,
    ReflexTrigger,
    RoomCreate,
    StateChange,
    TimeWindow,
)

NOW = datetime(2026, 11, 19, 21, 0, tzinfo=UTC)  # Thursday


def bulb(id: str, power: str, brightness: int = 100) -> dict[str, Any]:
    return {"id": id, "name": id, "type": "bulb", "capabilities": [
        {"type": "power", "value": power, "availableValues": ["on", "off"]},
        {"type": "brightness", "value": brightness, "min": 1, "max": 100}]}


def house() -> list[dict[str, Any]]:
    return [
        bulb("k_light", "on"),
        bulb("c_light", "off"),
        {"id": "motion1", "name": "Corredor", "type": "motionSensor",
         "capabilities": [{"type": "motionDetected", "value": False}]},
        {"id": "door1", "name": "Porta", "type": "contactSensor", "categories": ["security"],
         "capabilities": [{"type": "contact", "value": True}]},
        {"id": "tv1", "name": "TV", "type": "tv", "capabilities": [{"type": "power", "value": "off"}]},
    ]


def setv(srv, device_id: str, cap: str, value: Any) -> None:
    for c in srv.state[device_id]["capabilities"]:
        if c["type"] == cap:
            c["value"] = value


def getv(srv, device_id: str, cap: str) -> Any:
    return next(c["value"] for c in srv.state[device_id]["capabilities"] if c["type"] == cap)


# --- scripted model --------------------------------------------------------------------------

SCRIPT: dict[str, Any] = {}
PROMPTS: list[str] = []


def _prompt(messages: list[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    prompt = _prompt(messages)
    PROMPTS.append(prompt)
    out: dict[str, Any] = {"explanation": "Nada a fazer.", "beliefState": "b", "actions": []}
    if '"deviceId": "motion1", "capability": "motionDetected", "old": false, "new": true' in prompt:
        out = {
            "explanation": "Apaguei a luz da cozinha e acendi a do corredor.",
            "beliefState": "User walked from kitchen to corridor.",
            "actions": [
                {"deviceId": "k_light", "update": {"type": "power", "value": "off"}},
                {"deviceId": "c_light", "update": {"type": "power", "value": "on"}},
            ],
        }
        m = re.search(r'"id": "(\w+)", "statement"', prompt)
        if m and SCRIPT.get("propose_reflex") and "Reflexes:" not in prompt:
            out["reflexProposals"] = [{
                "name": "Luz segue-te para o corredor",
                "triggers": [{"deviceId": "motion1", "capability": "motionDetected",
                              "operator": "=", "value": True}],
                "actions": [
                    {"deviceId": "k_light", "update": {"type": "power", "value": "off"}},
                    {"deviceId": "c_light", "update": {"type": "power", "value": "on"}},
                    {"deviceId": "tv1", "update": {"type": "power", "value": "on"}},  # suggest
                ],
                "sourceInsightId": m.group(1),
                "rationale": "seen many times",
            }]
    elif SCRIPT.get("dim"):
        out["actions"] = [{"deviceId": "k_light", "update": {"type": "brightness", "value": 30}}]
    elif "Arma o alarme" in prompt:
        out["actions"] = [{"deviceId": "gw", "update": {"type": "securityStatus", "value": "away"}}]
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


@pytest.fixture
async def home():
    SCRIPT.clear()
    PROMPTS.clear()
    srv = fake_server(house(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="",
                        NOW_OVERRIDE=None, EMPTY_AFTER_MINUTES=0.5)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(scripted)
    )
    services.scheduler._min_gap = 0
    await services.poller.poll_once(DeviceSource.sim)  # baseline
    yield services, srv
    await services.aclose()


async def step(services, srv, device_id: str, cap: str, value: Any) -> list[StateChange]:
    setv(srv, device_id, cap, value)
    changes = await services.poller.poll_once(DeviceSource.sim)
    await services.scheduler.wait_idle()
    return changes


# --- insights --------------------------------------------------------------------------------


async def test_insight_lifecycle(home):
    services, _ = home
    eng = services.insights
    [i] = await eng.apply([InsightUpdate(op="create", statement="TV ao serão -> luz baixa")])
    assert i.confidence == pytest.approx(0.4)
    [i] = await eng.apply([InsightUpdate(op="reinforce", insightId=i.id, delta=0.9)])
    assert i.confidence == 1.0  # clamped
    [i] = await eng.apply([InsightUpdate(op="weaken", insightId=i.id, delta=0.85)])
    assert i.status == InsightStatus.dropped  # below INSIGHT_MIN_CONFIDENCE (0.2)
    assert await services.agent.list_insights() == []
    assert await eng.apply([InsightUpdate(op="reinforce", insightId="nope")]) == []


# --- reflex triggers (pure) ------------------------------------------------------------------


def reflex(triggers: list[ReflexTrigger], logic: str = "all") -> Reflex:
    return Reflex(id="r", name="r", triggers=triggers, triggerLogic=logic, actions=[],
                  createdAt=NOW, status=ReflexStatus.active)


def ch(device: str, cap: str, new: Any) -> StateChange:
    return StateChange(id="c", deviceId=device, capability=cap, new=new, at=NOW)


def test_trigger_logic_and_state():
    motion = ReflexTrigger(deviceId="m", capability="motionDetected", operator="=", value=True)
    dark = ReflexTrigger(deviceId="lux", capability="lux", operator="<", value=10)
    r = reflex([motion, dark])
    assert is_triggered(r, [ch("m", "motionDetected", True)], {("lux", "lux"): 3}, NOW)
    assert not is_triggered(r, [ch("m", "motionDetected", True)], {("lux", "lux"): 50}, NOW)
    assert not is_triggered(r, [], {("m", "motionDetected"): True, ("lux", "lux"): 3}, NOW)
    assert is_triggered(reflex([motion, dark], "any"), [ch("lux", "lux", 2)], {}, NOW)


def test_trigger_time_window_wraps_midnight():
    night = TimeWindow(**{"from": "22:00", "to": "06:00"})
    t = ReflexTrigger(deviceId="m", capability="motionDetected", operator="changed", timeWindow=night)
    c = [ch("m", "motionDetected", True)]
    assert is_triggered(reflex([t]), c, {}, NOW.replace(hour=23))
    assert is_triggered(reflex([t]), c, {}, NOW.replace(hour=5))
    assert not is_triggered(reflex([t]), c, {}, NOW.replace(hour=12))
    weekend = TimeWindow(**{"from": "00:00", "to": "23:59", "weekdays": [5, 6]})
    t2 = t.model_copy(update={"timeWindow": weekend})
    assert not is_triggered(reflex([t2]), c, {}, NOW)  # Thursday


# --- S2: agent decides, proposes a reflex, user confirms, reflex runs without the LLM ----------


async def test_s2_reflex_learned_confirmed_and_auto_paused(home):
    services, srv = home
    SCRIPT["propose_reflex"] = True
    [ins] = await services.insights.apply(
        [InsightUpdate(op="create", statement="Ao entrar no corredor, a luz segue o utilizador",
                       delta=0.85)])

    await step(services, srv, "motion1", "motionDetected", True)
    assert getv(srv, "k_light", "power") == "off" and getv(srv, "c_light", "power") == "on"
    [proposed] = await services.agent.list_reflexes("proposed")
    assert proposed.sourceInsightId == ins.id
    assert [a.deviceId for a in proposed.actions] == ["k_light", "c_light"]  # tv (suggest) dropped

    # Proposed reflexes never run
    for dev, cap, val in [("k_light", "power", "on"), ("c_light", "power", "off"),
                          ("motion1", "motionDetected", False)]:
        await step(services, srv, dev, cap, val)
    # Turning the corridor light off by hand overrides the agent (D9): the user unblocks it.
    assert services.feedback.in_cooldown("c_light")
    await services.feedback.clear_cooldown("c_light")
    await services.agent.confirm_reflex(proposed.id)

    # Next walk: the reflex fires inside the poll, before the agent runs
    setv(srv, "motion1", "motionDetected", True)
    changes = await services.poller.poll_once(DeviceSource.sim)
    assert getv(srv, "c_light", "power") == "on" and getv(srv, "k_light", "power") == "off"
    assert any(c.capability == "reflex_executed" for c in changes)
    await services.scheduler.wait_idle()
    assert "reflex_executed" in PROMPTS[-1]
    [r] = await services.agent.list_reflexes("active")
    assert r.executions == 1

    # Two undos of reflex actions -> auto-paused (REFLEX_MAX_UNDOS = 2), insight weakened
    reflex_actions = [a for a in await services.actions.list_actions("executed", 50)
                      if a.decisionId == f"reflex:{r.id}"]
    for a in reflex_actions[:2]:
        await services.actions.undo(a.id)
    [paused] = await services.agent.list_reflexes("paused")
    assert paused.undos == 2
    weakened = await services.insights.get(ins.id)
    assert weakened.confidence < ins.confidence


async def test_reflex_proposal_needs_confident_insight(home):
    services, srv = home
    SCRIPT["propose_reflex"] = True
    await services.insights.apply([InsightUpdate(op="create", statement="talvez", delta=0.5)])
    await step(services, srv, "motion1", "motionDetected", True)
    assert await services.agent.list_reflexes(None) == []


# --- D9: manual override -> feedback + cooldown ------------------------------------------------


async def test_manual_override_sets_cooldown(home):
    services, srv = home
    SCRIPT["dim"] = True
    await step(services, srv, "tv1", "power", "on")
    assert getv(srv, "k_light", "brightness") == 30
    await step(services, srv, "k_light", "brightness", 30)  # no-op poll (already 30)

    await step(services, srv, "k_light", "brightness", 90)  # user turns it back up
    fb = await services.repo.recent_feedback(5)
    assert fb[0].kind == "manual_override"
    assert services.feedback.in_cooldown("k_light")

    await step(services, srv, "tv1", "power", "off")  # agent wants to dim again -> skipped
    assert getv(srv, "k_light", "brightness") == 90
    assert "Override cooldown (leave these devices alone): k_light" in PROMPTS[-1]

    resp = await services.agent.chat(ChatRequest(message="baixa a luz"))  # explicit command
    assert resp.decision is not None
    assert getv(srv, "k_light", "brightness") == 30


# --- D14: presence facts and the door timer ----------------------------------------------------


async def test_presence_facts_raw(home):
    services, _ = home
    hall = await services.rooms.create_room(RoomCreate(name="Entrada", isEntrance=True))
    await services.devices.assign("door1", DeviceAssignment(roomId=hall.id))
    now = datetime.now(UTC)

    def c(id: str, dev: str, cap: str, new: Any, secs_ago: float) -> StateChange:
        return StateChange(id=id, deviceId=dev, capability=cap, new=new,
                           at=now - timedelta(seconds=secs_ago))

    await services.repo.add_changes([
        c("1", "motion1", "motionDetected", True, 120),
        c("2", "door1", "contact", False, 100),  # opened
        c("3", "door1", "contact", True, 95),  # closed
    ])
    facts = await compute_presence_facts(services.devices, services.repo, 30)
    assert facts.lastDoorEvent and facts.lastDoorEvent.closedAt is not None
    assert facts.lastDoorEvent.roomId == hall.id
    assert facts.motionAfterDoorEvent is False
    assert facts.secondsSinceLastDoorEvent == pytest.approx(95, abs=2)
    assert facts.emptyAfterSeconds == 30
    assert "isEmpty" not in facts.model_dump()

    await services.repo.add_changes([c("4", "motion1", "motionDetected", True, 10)])
    facts = await compute_presence_facts(services.devices, services.repo, 30)
    assert facts.motionAfterDoorEvent is True


async def test_presence_watcher_wakes_agent_after_door_event(home):
    services, _ = home
    hall = await services.rooms.create_room(RoomCreate(name="Entrada", isEntrance=True))
    await services.devices.assign("door1", DeviceAssignment(roomId=hall.id))
    reasons: list[str] = []

    async def wake(reason: str) -> None:
        reasons.append(reason)

    watcher = PresenceWatcher(services.devices, services.repo, -0.9, wake)  # fires after ~0.1 s
    await watcher.on_changes([ch("door1", "contact", True)])
    await watcher.on_changes([ch("motion1", "motionDetected", True)])  # not a door: ignored
    await asyncio.sleep(0.3)
    assert len(reasons) == 1 and "get_presence_facts" in reasons[0]


async def test_wake_runs_agent_without_changes(home):
    services, _ = home
    await services.agent.wake("timer test")
    await services.scheduler.wait_idle()
    assert "Why you were woken up:\n- timer test" in PROMPTS[-1]


# --- alarm: always suggest, written through patch_home_security_status ------------------------


async def test_alarm_is_suggest_then_patch_security_status():
    SCRIPT.clear()
    gw = {"id": "gw", "name": "Security hub", "type": "gateway", "categories": ["security"],
          "capabilities": [{"type": "alarmState", "value": "normal",
                            "availableValues": ["normal", "alarm"]}]}
    srv = fake_server([gw])
    calls: list[dict[str, Any]] = []

    @srv.tool()
    def patch_home_security_status(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        return {"status": body["status"].upper()}

    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="")
    services = await build_services(
        settings, clients={DeviceSource.altice: AlticeClient(srv)}, model=FunctionModel(scripted)
    )
    try:
        await services.devices.list_devices()
        await services.agent.chat(ChatRequest(message="Arma o alarme"))
        [pending] = await services.actions.list_actions(None, 10)
        assert pending.status == ActionStatus.pending_confirmation and calls == []
        done = await services.actions.confirm(pending.id)
        assert done.status == ActionStatus.executed
        assert calls and calls[0]["status"] == "away"
    finally:
        await services.aclose()

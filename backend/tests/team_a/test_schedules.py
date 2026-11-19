"""Team A: scheduled reflexes (time / first_motion), user-created schedules, routine learning
relative to waking up, and the daily routine review. No real LLM: scripted or forbidden models."""
from __future__ import annotations

import json
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

from app.agent.reflexes import CATCH_UP, is_due
from app.agent.tools import routine_stats
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import (
    CapabilityUpdate,
    ChatRequest,
    DeviceSource,
    ProposedAction,
    Reflex,
    ReflexCreate,
    ReflexStatus,
    ReflexTrigger,
    StateChange,
)

MORNING = datetime(2026, 11, 20, 7, 30, 5)  # Friday, naive house time like NOW_OVERRIDE


def reflex(*triggers: ReflexTrigger, logic: str = "all", last: datetime | None = None) -> Reflex:
    return Reflex(id="r", name="r", triggers=list(triggers), triggerLogic=logic, actions=[],
                  status=ReflexStatus.active, createdAt=MORNING, lastFiredAt=last)


AT_0730 = ReflexTrigger(kind="time", at="07:30", weekdays=[0, 1, 2, 3, 4])


# --- is_due (pure) ---------------------------------------------------------------------------


def test_time_trigger_due_window_and_weekdays():
    r = reflex(AT_0730)
    assert not is_due(r, MORNING.replace(minute=29), None, {})
    assert is_due(r, MORNING, None, {})
    assert is_due(r, MORNING + CATCH_UP - timedelta(seconds=10), None, {})  # after a restart
    assert not is_due(r, MORNING + CATCH_UP + timedelta(minutes=1), None, {})  # too late today
    assert not is_due(r, MORNING + timedelta(days=1), None, {})  # Saturday


def test_fires_once_per_house_day():
    assert not is_due(reflex(AT_0730, last=MORNING), MORNING + timedelta(minutes=1), None, {})
    yesterday = MORNING - timedelta(days=3)  # Tuesday
    assert is_due(reflex(AT_0730, last=yesterday), MORNING, None, {})


def test_state_conditions_next_to_a_schedule():
    light_off = ReflexTrigger(deviceId="lamp", capability="power", operator="=", value="off")
    r = reflex(AT_0730, light_off)
    assert is_due(r, MORNING, None, {("lamp", "power"): "off"})
    assert not is_due(r, MORNING, None, {("lamp", "power"): "on"})


def test_first_motion_trigger():
    r = reflex(ReflexTrigger(kind="first_motion", delayMinutes=30))
    woke = MORNING.replace(hour=7, minute=0, second=0)
    assert not is_due(r, MORNING, None, {})  # nobody moved yet
    assert not is_due(r, woke + timedelta(minutes=29), woke, {})
    assert is_due(r, woke + timedelta(minutes=30, seconds=5), woke, {})


# --- routine stats relative to waking up -------------------------------------------------------


def test_routine_stats_detect_after_first_motion():
    today = datetime(2026, 11, 20, 12, tzinfo=UTC)
    plug, motion = [], []
    for i, wake_min in enumerate([0, 25, 50, 10, 40, 15, 35, 5, 45, 20], start=1):
        day = today - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        woke = day.replace(hour=6, minute=30) + timedelta(minutes=wake_min)
        motion.append(StateChange(id=f"m{i}", deviceId="mot", capability="motionDetected",
                                  old=False, new=True, at=woke))
        on = woke + timedelta(minutes=30)
        plug += [StateChange(id=f"on{i}", deviceId="p", capability="instantPower", old=2, new=1200,
                             at=on),
                 StateChange(id=f"off{i}", deviceId="p", capability="instantPower", old=1200, new=2,
                             at=on + timedelta(minutes=2))]
    plug.sort(key=lambda c: c.at)
    stats = routine_stats(plug, "p", "instantPower", 14, 20, today, motion)
    wd = stats["weekdays"]
    assert wd["afterFirstMotion"]["medianMin"] == 30.0
    assert wd["afterFirstMotion"]["spreadMin"] == 0.0  # always 30 min after waking up
    assert wd["spreadMin"] >= 30  # while the clock time varies a lot


# --- engine + services (no LLM allowed) -------------------------------------------------------


def forbidden(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise AssertionError("a scheduled reflex must not call the LLM")


def house() -> list[dict[str, Any]]:
    return [
        {"id": "lamp", "name": "Luz do quarto", "type": "bulb", "capabilities": [
            {"type": "power", "value": "off", "availableValues": ["on", "off"]}]},
        {"id": "tv1", "name": "TV", "type": "tv", "capabilities": [{"type": "power", "value": "off"}]},
        {"id": "mot", "name": "Movimento", "type": "motionSensor",
         "capabilities": [{"type": "motionDetected", "value": False}]},
    ]


@pytest.fixture
async def home(request):
    now = getattr(request, "param", None)
    srv = fake_server(house(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="", NOW_OVERRIDE=now)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(forbidden)
    )
    await services.poller.poll_once(DeviceSource.sim)
    yield services, srv
    await services.aclose()


def lamp(srv) -> str:
    return srv.state["lamp"]["capabilities"][0]["value"]


WAKE_LIGHT = ReflexCreate(
    name="Acordar com luz",
    triggers=[ReflexTrigger(kind="time", at="07:30")],
    actions=[ProposedAction(deviceId="lamp", update=CapabilityUpdate(type="power", value="on"))],
)


@pytest.mark.parametrize("home", ["2026-11-20T07:30:05"], indirect=True)
async def test_user_schedule_runs_without_llm_once_a_day(home):
    services, srv = home
    r = await services.agent.create_reflex(WAKE_LIGHT)
    assert r.status == ReflexStatus.active  # created by the user: no confirmation step
    changes = await services.reflexes.tick()
    assert lamp(srv) == "on" and [c.capability for c in changes] == ["reflex_executed"]
    [stored] = await services.agent.list_reflexes("active")
    assert stored.executions == 1 and stored.lastFiredAt is not None
    srv.state["lamp"]["capabilities"][0]["value"] = "off"  # user turns it off again
    await services.poller.poll_once(DeviceSource.sim)
    assert await services.reflexes.tick() == []  # not twice the same day
    assert lamp(srv) == "off"


@pytest.mark.parametrize("home", ["2026-11-20T07:00:00"], indirect=True)
async def test_schedule_not_due_does_nothing(home):
    services, srv = home
    await services.agent.create_reflex(WAKE_LIGHT)
    assert await services.reflexes.tick() == [] and lamp(srv) == "off"


async def test_user_schedule_refuses_unsafe_or_invalid_actions(home):
    services, _ = home
    tv_on = WAKE_LIGHT.model_copy(update={"actions": [
        ProposedAction(deviceId="tv1", update=CapabilityUpdate(type="power", value="on"))]})
    with pytest.raises(ValueError, match="confirmation"):  # tv.power is suggest -> 409
        await services.agent.create_reflex(tv_on)
    bogus = WAKE_LIGHT.model_copy(update={"actions": [
        ProposedAction(deviceId="lamp", update=CapabilityUpdate(type="flux", value=1))]})
    with pytest.raises(ValueError):  # unknown capability -> 409
        await services.agent.create_reflex(bogus)
    ghost = WAKE_LIGHT.model_copy(update={"actions": [
        ProposedAction(deviceId="ghost", update=CapabilityUpdate(type="power", value="on"))]})
    with pytest.raises(KeyError):  # -> 404
        await services.agent.create_reflex(ghost)


async def test_first_motion_schedule_fires_after_waking_up(home):
    services, srv = home  # real clock (no NOW_OVERRIDE)
    coffee_after_waking = ReflexCreate(
        name="Luz 1 min depois de acordar",
        triggers=[ReflexTrigger(kind="first_motion", delayMinutes=1)],
        actions=WAKE_LIGHT.actions,
    )
    await services.agent.create_reflex(coffee_after_waking)
    assert await services.reflexes.tick() == []  # nobody moved today yet
    await services.repo.add_changes([StateChange(
        id="m1", deviceId="mot", capability="motionDetected", old=False, new=True,
        at=datetime.now(UTC) - timedelta(minutes=2))])
    await services.reflexes.tick()
    assert lamp(srv) == "on"


# --- the agent proposes schedules (scripted model) ---------------------------------------------


def _prompt(messages: list[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


PROMPTS: list[str] = []


def proposer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    prompt = _prompt(messages)
    PROMPTS.append(prompt)
    out: dict[str, Any] = {"explanation": "Nada.", "beliefState": "b", "actions": []}
    draft = {"name": "Luz às 7:30", "triggers": [{"kind": "time", "at": "07:30"}],
             "actions": [{"deviceId": "lamp", "update": {"type": "power", "value": "on"}}]}
    if "acende a luz do quarto às 7:30" in prompt:
        out = {"explanation": "Criei o agendamento; confirma-o no separador Aprendizagem.",
               "beliefState": "b", "reflexProposals": [draft]}
    elif "Routine stats of the plugs" in prompt:
        out = {"explanation": "Aprendi a rotina.", "beliefState": "b",
               "insightUpdates": [{"op": "create", "statement": "Luz às 7:30", "delta": 0.85}],
               "reflexProposals": [{**draft, "sourceInsightId": ""}]}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


@pytest.fixture
async def agent_home(request):
    PROMPTS.clear()
    now = getattr(request, "param", None)
    srv = fake_server(house(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="", NOW_OVERRIDE=now)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(proposer)
    )
    services.scheduler._min_gap = 0
    await services.poller.poll_once(DeviceSource.sim)
    yield services, srv
    await services.aclose()


async def test_chat_request_becomes_a_proposed_schedule(agent_home):
    services, _ = agent_home
    resp = await services.agent.chat(ChatRequest(message="acende a luz do quarto às 7:30"))
    [r] = resp.decision.reflexProposals
    assert r.reflex.status == ReflexStatus.proposed and r.reflex.triggers[0].kind == "time"
    assert r.reflex.sourceInsightId is None  # user request: no insight needed


async def test_learned_routine_links_the_new_insight(agent_home):
    services, _ = agent_home
    await services.on_ready()  # startup = routine review
    await services.scheduler.wait_idle()
    [r] = await services.agent.list_reflexes("proposed")
    [insight] = await services.agent.list_insights()
    assert r.sourceInsightId == insight.id and insight.confidence == pytest.approx(0.85)


@pytest.mark.parametrize("agent_home", ["2026-11-20T04:10:00"], indirect=True)
async def test_daily_review_once_per_house_day(agent_home):
    services, _ = agent_home
    assert await services.agent.maybe_daily_review() is True
    await services.scheduler.wait_idle()
    assert "Daily routine review" in PROMPTS[-1] and "Routine stats of the plugs" in PROMPTS[-1]
    assert await services.agent.maybe_daily_review() is False  # already done today


@pytest.mark.parametrize("agent_home", ["2026-11-20T03:10:00"], indirect=True)
async def test_no_daily_review_before_its_hour(agent_home):
    services, _ = agent_home
    assert await services.agent.maybe_daily_review() is False

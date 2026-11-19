"""Team A phase 3: seed import, routine stats, agent-scheduled wake-ups, S3 coffee routine."""
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

from app.agent.agent import Clock, WakeScheduler
from app.agent.tools import routine_stats
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import ActionStatus, AutonomyLevel, DecisionTrigger, DeviceSource, StateChange
from app.storage.db import init_db
from app.storage.import_seed import align_to_today, import_seed, parse_lines
from app.storage.repository import Repository


def coffee_history(today: datetime, days: int = 14) -> list[StateChange]:
    """Weekday mornings: 2 W -> 1200 W at ~07:30 UTC, back to 2 W two minutes later."""
    out: list[StateChange] = []
    for i in range(1, days + 1):
        day = (today - timedelta(days=i)).replace(hour=7, minute=28 + i % 5, second=0, microsecond=0)
        if day.weekday() >= 5:
            continue
        out.append(StateChange(id=f"on{i}", deviceId="coffee", capability="instantPower",
                               old=2, new=1200, at=day))
        out.append(StateChange(id=f"off{i}", deviceId="coffee", capability="instantPower",
                               old=1200, new=2, at=day + timedelta(minutes=2)))
    return out


# --- seed import -----------------------------------------------------------------------------


async def test_import_seed_idempotent_and_aligned(tmp_path):
    old = datetime(2026, 1, 10, 7, 30, tzinfo=UTC)
    lines = [c.model_dump_json() for c in coffee_history(old)]
    lines.append(json.dumps({"deviceId": "tv1", "capability": "power", "old": "off",
                             "new": "on", "at": "2026-01-09T21:00:00"}))  # no id, naive time
    lines += ["", "# comment", "{not json", json.dumps({"deviceId": "x"})]
    path = tmp_path / "history.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    repo = Repository(await init_db(":memory:"))
    first = await import_seed(str(path), repo=repo)
    again = await import_seed(str(path), repo=repo)
    assert first.imported == again.imported == len(coffee_history(old)) + 1
    assert first.skipped == 2
    assert len(await repo.list_changes(None, None, 1000)) == first.imported  # idempotent
    await repo.close()

    changes, _ = parse_lines(lines)
    shift = align_to_today(changes, today=datetime(2026, 1, 20, 12, tzinfo=UTC))
    assert shift == 11
    latest = max(c.at for c in changes)
    assert latest.date().isoformat() == "2026-01-20" and latest.hour == 21  # time of day kept


# --- routine stats ---------------------------------------------------------------------------


def test_routine_stats_weekday_mornings():
    today = datetime(2026, 11, 19, 12, tzinfo=UTC)  # Thursday
    stats = routine_stats(coffee_history(today), "coffee", "instantPower", 14, 20, today)
    wd = stats["weekdays"]
    # the 14-day window includes today, so the oldest seeded weekday falls outside it
    assert wd["activeDays"] == 9 and wd["days"] == 10
    assert "07:28" <= wd["medianFirstOn"] <= "07:32" and wd["spreadMin"] <= 4
    assert wd["medianDurationMin"] == 2.0
    assert stats["weekends"]["activeDays"] == 0
    assert stats["byDay"][-1]["firstOn"] is None  # nothing today yet


def test_routine_stats_power_strings():
    today = datetime(2026, 11, 19, 12, tzinfo=UTC)
    on = today.replace(hour=8) - timedelta(days=1)
    changes = [
        StateChange(id="a", deviceId="p", capability="power", old="off", new="on", at=on),
        StateChange(id="b", deviceId="p", capability="power", old="on", new="off",
                    at=on + timedelta(minutes=30)),
    ]
    stats = routine_stats(changes, "p", "power", 7, 20, today)
    assert stats["weekdays"]["medianFirstOn"] == "08:00"
    assert stats["weekdays"]["medianDurationMin"] == 30.0


# --- wake-ups --------------------------------------------------------------------------------


async def test_wake_scheduler_resolve_and_fire():
    fired: list[str] = []

    async def wake(reason: str) -> None:
        fired.append(reason)

    clock = Clock("2026-11-20T07:20:00")  # naive house time, as in NOW_OVERRIDE
    ws = WakeScheduler(clock, wake)
    assert ws.resolve("07:25") == datetime(2026, 11, 20, 7, 25)
    assert ws.resolve("07:00") == datetime(2026, 11, 21, 7, 0)  # already passed -> tomorrow
    assert ws.resolve("bogus") is None
    assert ws.schedule("2026-11-25T07:00:00", "too far") is None

    soon = (clock.now() + timedelta(seconds=0.1)).isoformat()
    ws.schedule(soon, "coffee")
    ws.schedule(soon, "coffee again")  # same minute -> deduplicated
    assert len(ws.pending()) == 1
    await asyncio.sleep(0.3)
    assert fired == ["Scheduled wake-up you asked for: coffee"]
    ws.cancel_all()


# --- S3 end to end with a scripted model -----------------------------------------------------

SEEN: dict[str, Any] = {}


def _user_prompt(messages: list[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    prompt = _user_prompt(messages)
    final = info.output_tools[0].name
    out: dict[str, Any] = {"explanation": "Nada a fazer.", "beliefState": "b", "actions": []}
    if "The backend just started" in prompt:
        # Routine stats are inlined in the startup prompt (no tool round-trip).
        assert not info.function_tools
        line = re.search(r"Routine stats of the plugs \([^)]*\):\n(.+)", prompt).group(1)
        SEEN["stats"] = json.loads(line)[0]
        now = datetime.fromisoformat(re.search(r"Now \(house local time\): (\S+)", prompt).group(1))
        out = {
            "explanation": "Aprendi que costumas fazer café por volta das 07:30 nos dias úteis.",
            "beliefState": "Coffee routine on weekdays ~07:30.",
            "insightUpdates": [{"op": "create", "delta": 0.8,
                                "statement": "Café nos dias úteis por volta das 07:30"}],
            "wakeUps": [{"at": (now + timedelta(seconds=0.2)).isoformat(),
                         "reason": "coffee routine"}],
        }
    elif "Scheduled wake-up you asked for: coffee routine" in prompt:
        out = {
            "explanation": "Liguei a máquina do café, como nos outros dias úteis.",
            "beliefState": "Coffee machine on for the morning routine.",
            "actions": [{"deviceId": "coffee", "update": {"type": "power", "value": "on"}}],
        }
    return ModelResponse(parts=[ToolCallPart(final, json.dumps(out))])


@pytest.fixture
async def home():
    SEEN.clear()
    plug = {"id": "coffee", "name": "Máquina do café", "type": "plug", "categories": ["energy"],
            "capabilities": [{"type": "power", "value": "off", "availableValues": ["on", "off"]},
                             {"type": "instantPower", "value": 2}]}
    srv = fake_server([plug], with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="", NOW_OVERRIDE=None)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(scripted)
    )
    services.scheduler._min_gap = 0
    await services.repo.add_changes(coffee_history(datetime.now(UTC)))
    yield services, srv
    await services.aclose()


async def test_s3_coffee_routine(home):
    services, srv = home
    await services.poller.poll_once(DeviceSource.sim)  # baseline
    await services.on_ready()  # startup run, as the poller does after its first round
    await services.scheduler.wait_idle()

    assert SEEN["stats"]["weekdays"]["activeDays"] >= 9
    [decision] = await services.actions.list_decisions(10)
    assert decision.trigger == DecisionTrigger.startup
    assert [i.statement for i in await services.agent.list_insights()] == [
        "Café nos dias úteis por volta das 07:30"]
    assert len(services.agent.wakeups.pending()) == 1

    await asyncio.sleep(0.4)  # the scheduled wake-up fires
    await services.scheduler.wait_idle()
    plug_power = srv.state["coffee"]["capabilities"][0]["value"]
    assert plug_power == "on"
    [action] = await services.actions.list_actions(None, 10)
    assert action.status == ActionStatus.executed and action.autonomy == AutonomyLevel.auto_notify

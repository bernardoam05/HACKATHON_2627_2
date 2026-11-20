"""Team A core: diff, anti-loop, scheduler, storage, plugins, rooms."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.devices.rooms import RoomStore
from app.plugins.registry import PluginRegistry
from app.poller.antiloop import tag_agent_changes
from app.poller.diff import DEVICE_PRESENCE, diff, diff_snapshots
from app.poller.scheduler import LatestWinsScheduler
from app.schemas import (
    ActionRecord,
    ActionStatus,
    AutonomyLevel,
    CapabilityUpdate,
    Decision,
    DecisionTrigger,
    Device,
    DevicePlugin,
    DeviceSource,
    PluginStatus,
    RoomCreate,
    StateChange,
    StateSnapshot,
)
from app.storage.db import init_db
from app.storage.repository import Repository

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
T0 = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)


def fixture_devices() -> list[Device]:
    data = json.loads((FIXTURES / "get_home_devices.json").read_text())
    return [Device.model_validate(d) for d in data["devices"]]


def snap(devices: list[Device], at: datetime = T0, id: str = "s") -> StateSnapshot:
    return StateSnapshot(id=id, takenAt=at, devices=devices)


def bulb(brightness: int = 100, power: str = "on", id: str = "b1") -> Device:
    return Device.model_validate(
        {
            "id": id,
            "type": "bulb",
            "source": "sim",
            "capabilities": [
                {"type": "brightness", "value": brightness, "min": 1, "max": 100},
                {"type": "power", "value": power, "availableValues": ["on", "off"]},
            ],
        }
    )


@pytest.fixture
async def repo():
    r = Repository(await init_db(":memory:"))
    yield r
    await r.close()


# --- diff -----------------------------------------------------------------------------------


def test_diff_no_baseline_is_empty():
    assert diff(None, snap([bulb()])) == []


def test_diff_capability_change():
    changes = diff(snap([bulb(100)]), snap([bulb(30)], T0 + timedelta(seconds=2)))
    assert [(c.deviceId, c.capability, c.old, c.new) for c in changes] == [
        ("b1", "brightness", 100, 30)
    ]
    assert changes[0].at == T0 + timedelta(seconds=2)


def test_diff_added_and_removed():
    d = diff_snapshots(snap([bulb(id="a")]), snap([bulb(id="b")]))
    assert [x.id for x in d.added] == ["b"] and [x.id for x in d.removed] == ["a"]
    assert {(c.deviceId, c.capability, c.new) for c in d.changes} == {
        ("b", DEVICE_PRESENCE, "present"),
        ("a", DEVICE_PRESENCE, "absent"),
    }


def test_diff_identical_fixture_snapshots():
    assert diff(snap(fixture_devices()), snap(fixture_devices())) == []


# --- anti-loop ------------------------------------------------------------------------------


def action(expected, at: datetime = T0, cap: str = "brightness", status=ActionStatus.executed):
    return ActionRecord(
        id="a1",
        decisionId="d1",
        deviceId="b1",
        update=CapabilityUpdate(type=cap, value=expected),
        autonomy=AutonomyLevel.auto,
        status=status,
        expectedValue=expected,
        createdAt=at,
    )


def change(old, new, at: datetime, cap: str = "brightness") -> StateChange:
    return StateChange(id="c", deviceId="b1", capability=cap, old=old, new=new, at=at)


def test_antiloop_exact_match_within_window():
    [c] = tag_agent_changes([change(100, 30, T0 + timedelta(seconds=3))], [action(30)])
    assert c.causedByAgent and c.actionId == "a1"


def test_antiloop_fade_intermediate_value():
    [c] = tag_agent_changes([change(100, 60, T0 + timedelta(seconds=2))], [action(30)])
    assert c.causedByAgent


def test_antiloop_outside_window_or_other_value():
    late = tag_agent_changes([change(100, 30, T0 + timedelta(seconds=20))], [action(30)])
    other = tag_agent_changes([change(30, 80, T0 + timedelta(seconds=2))], [action(30)])
    pending = tag_agent_changes(
        [change(100, 30, T0 + timedelta(seconds=2))],
        [action(30, status=ActionStatus.pending_confirmation)],
    )
    assert not late[0].causedByAgent
    assert not other[0].causedByAgent
    assert not pending[0].causedByAgent


def test_antiloop_string_value():
    [c] = tag_agent_changes(
        [change("on", "off", T0 + timedelta(seconds=1), cap="power")], [action("off", cap="power")]
    )
    assert c.causedByAgent


# --- scheduler ------------------------------------------------------------------------------


async def test_scheduler_latest_wins():
    runs = 0
    gate = asyncio.Event()
    statuses: list[tuple[bool, bool]] = []

    async def run() -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            await gate.wait()

    async def on_status(busy: bool, dirty: bool) -> None:
        statuses.append((busy, dirty))

    s = LatestWinsScheduler(run, on_status, min_gap_seconds=0)
    await s.request_run()
    await asyncio.sleep(0)
    for _ in range(5):  # many changes while busy -> exactly one rerun
        await s.request_run()
    assert s.busy and s.dirty
    gate.set()
    await s.wait_idle()
    assert runs == 2
    assert not s.busy and not s.dirty
    assert statuses[0] == (True, False) and statuses[-1] == (False, False)


async def test_scheduler_survives_run_error():
    async def boom() -> None:
        raise RuntimeError("llm down")

    s = LatestWinsScheduler(boom, min_gap_seconds=0)
    await s.request_run()
    await s.wait_idle()
    assert not s.busy and s.runs == 1


# --- storage --------------------------------------------------------------------------------


async def test_repo_snapshot_and_changes(repo: Repository):
    await repo.save_snapshot(snap(fixture_devices(), id="s1"))
    await repo.save_snapshot(snap([bulb()], T0 + timedelta(seconds=5), id="s2"))
    latest = await repo.latest_snapshot()
    assert latest and latest.id == "s2"

    c1 = change(100, 30, T0 + timedelta(seconds=1))
    c2 = change(30, 10, T0 + timedelta(seconds=9)).model_copy(update={"id": "c2"})
    await repo.add_changes([c1, c2])
    assert [c.id for c in await repo.list_changes(None, None, 10)] == ["c2", "c"]
    since = (T0 + timedelta(seconds=5)).isoformat()
    assert [c.id for c in await repo.list_changes("b1", since, 10)] == ["c2"]
    assert [c.id for c in await repo.changes_between(T0 + timedelta(seconds=1))] == ["c2"]


async def test_repo_actions_and_decisions(repo: Repository):
    a = action(30)
    await repo.save_action(a)
    assert (await repo.get_action("a1")) == a
    assert len(await repo.list_actions("executed", 10)) == 1
    assert await repo.list_actions("rejected", 10) == []
    d = Decision(id="d1", createdAt=T0, trigger=DecisionTrigger.state_change, explanation="ok")
    await repo.save_decision(d)
    assert (await repo.list_decisions(5))[0].id == "d1"


async def test_repo_belief_runs_since_full(repo: Repository):
    await repo.save_belief("a", "full", T0)
    await repo.save_belief("b", "delta", T0 + timedelta(seconds=1))
    await repo.save_belief("c", "delta", T0 + timedelta(seconds=2))
    assert await repo.runs_since_full() == 2
    latest = await repo.latest_belief()
    assert latest and latest[0] == "c"


# --- plugins --------------------------------------------------------------------------------


async def test_plugins_autonomy_resolution(repo: Repository):
    devices = fixture_devices()
    reg = PluginRegistry(repo, devices=lambda: _async(devices))
    by_type = {d.type: d for d in devices}
    assert await reg.resolve_autonomy(by_type["bulb"], "brightness") == AutonomyLevel.auto
    assert await reg.resolve_autonomy(by_type["plug"], "power") == AutonomyLevel.auto_notify
    # Security capabilities are always suggest
    assert await reg.resolve_autonomy(by_type["gateway"], "alarmState") == AutonomyLevel.suggest
    assert await reg.resolve_autonomy(by_type["camera"], "sirenSwitch") == AutonomyLevel.suggest
    # Unknown type -> category default -> suggest
    energy = Device(id="x", type="heater", categories=["energy"], source=DeviceSource.sim)
    unknown = Device(id="y", type="motionSensor", source=DeviceSource.sim)
    assert await reg.resolve_autonomy(energy, "power") == AutonomyLevel.auto_notify
    assert await reg.resolve_autonomy(unknown, "motionDetected") == AutonomyLevel.suggest


async def test_plugins_confirm_and_unconfigured(repo: Repository):
    devices = [*fixture_devices(), Device(id="m", type="motionSensor", source=DeviceSource.sim)]
    reg = PluginRegistry(repo, devices=lambda: _async(devices))
    assert {p.type for p in await reg.list_plugins()} >= {"bulb", "tv", "gateway"}
    assert await reg.unconfigured_types() == ["motionSensor"]
    plugin = DevicePlugin.model_validate(
        {
            "type": "motionSensor",
            "displayName": "Sensor de movimento",
            "purpose": "security",
            "capabilities": [
                {"type": "motionDetected", "writable": False, "autonomy": "auto"},
                {"type": "sirenSwitch", "writable": True, "autonomy": "auto"},
            ],
        }
    )
    confirmed = await reg.confirm_plugin("motionSensor", plugin)
    assert confirmed.status == PluginStatus.confirmed
    assert confirmed.autonomy_for("sirenSwitch") == AutonomyLevel.suggest
    assert await reg.unconfigured_types() == []


async def _async(value):
    return value


# --- rooms ----------------------------------------------------------------------------------


async def test_rooms_crud_unassigns_devices(repo: Repository):
    rooms = RoomStore(repo)
    hall = await rooms.create_room(RoomCreate(name="Entrada", isEntrance=True))
    await repo.set_override("b1", roomId=hall.id, alias="Luz")
    assert [r.name for r in await rooms.list_rooms()] == ["Entrada"]
    await rooms.delete_room(hall.id)
    assert await rooms.list_rooms() == []
    assert (await repo.get_overrides())["b1"] == {"alias": "Luz", "roomId": None, "enabled": None}


async def test_concurrent_proposals_share_one_llm_call(repo: Repository):
    """The config dialog may ask twice at once (React StrictMode): one LLM request, not two."""
    import asyncio

    from app.agent.onboarding import finalize, heuristic_draft

    devices = [Device(id="lock", type="fechaduraInteligente", name="Fechadura",
                      source=DeviceSource.sim)]
    calls = 0

    async def proposer(device: Device):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return finalize(device, heuristic_draft(device))

    reg = PluginRegistry(repo, devices=lambda: _async(devices), proposer=proposer)
    a, b = await asyncio.gather(reg.propose("fechaduraInteligente"),
                                reg.propose("fechaduraInteligente"))
    assert calls == 1 and a.plugin.type == b.plugin.type == "fechaduraInteligente"
    await reg.propose("fechaduraInteligente")  # a later request (e.g. "propose again") is new
    assert calls == 2

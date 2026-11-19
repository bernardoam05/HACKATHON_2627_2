"""Read-only agent tools.

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext

from app.devices.registry import DeviceRegistry
from app.plugins.registry import PluginRegistry
from app.schemas import DoorEvent, PresenceFacts, Room, RoomMotion, StateChange
from app.storage.repository import Repository

from .prompts import device_summary


@dataclass
class AgentDeps:
    registry: DeviceRegistry
    repo: Repository
    plugins: PluginRegistry
    now: Callable[[], datetime]
    empty_after_seconds: float = 600


def register_tools(agent: Agent[AgentDeps, Any]) -> None:
    """get_home_state, get_device, get_plugin, get_history, get_presence_facts, get_routine_stats.

    No write tools (D10).

    """

    @agent.tool
    async def get_routine_stats(
        ctx: RunContext[AgentDeps],
        device_id: str,
        capability: str,
        days: int = 14,
        threshold: float = 20,
    ) -> dict[str, Any]:
        """Daily usage of one capability over the last `days`: per day the first time it became
        active (power on, bool true, or numeric value > threshold, e.g. watts) and for how long,
        plus weekday/weekend medians and spread. Times are UTC. Use it to find routines."""
        return await compute_routine_stats(ctx.deps.repo, device_id, capability, days, threshold)

    @agent.tool
    async def get_presence_facts(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Raw presence facts: last entrance-door event, last motion per room, elapsed seconds and
        emptyAfterSeconds. Apply the empty-house rule yourself; there is no isEmpty field."""
        facts = await compute_presence_facts(
            ctx.deps.registry, ctx.deps.repo, ctx.deps.empty_after_seconds
        )
        return facts.model_dump(mode="json")

    @agent.tool
    async def get_home_state(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
        """Current state of every enabled device."""
        devices = await ctx.deps.registry.list_devices()
        return [device_summary(d) for d in devices if d.enabled]

    @agent.tool
    async def get_device(ctx: RunContext[AgentDeps], device_id: str) -> dict[str, Any]:
        """Current state of one device."""
        try:
            return device_summary(await ctx.deps.registry.get_device(device_id))
        except KeyError as e:
            raise ModelRetry(f"Unknown device id {device_id!r}") from e

    @agent.tool
    async def get_plugin(ctx: RunContext[AgentDeps], device_type: str) -> dict[str, Any] | None:
        """Capability descriptions, writability and autonomy for a device type (null if unknown)."""
        plugin = await ctx.deps.plugins.get(device_type)
        return plugin.model_dump(mode="json") if plugin else None

    @agent.tool
    async def get_history(
        ctx: RunContext[AgentDeps],
        device_id: str,
        capability: str | None = None,
        hours: float = 24,
    ) -> list[dict[str, Any]]:
        """Stored state changes for a device over the last `hours` (oldest first, max 200)."""
        since = datetime.now(UTC) - timedelta(hours=hours)  # history is stored in real UTC time
        changes = await ctx.deps.repo.changes_between(since, device_id=device_id, capability=capability)
        return [
            {"capability": c.capability, "old": c.old, "new": c.new, "at": c.at.isoformat(),
             "causedByAgent": c.causedByAgent}
            for c in changes[-200:]
        ]


# --- presence (D14): raw facts only, code never concludes "empty" ---------------------------

# Names as in backend/fixtures (Altice); the simulator uses the same ones (D1).
DOOR_CAPABILITIES = ("sensorState",)
MOTION_CAPABILITY = "motionDetected"


def door_is_open(capability: str, value: Any) -> bool | None:
    """contactSensor `sensorState`: "alarm" = open, "normal" = closed.
    None when the change is not a door reading."""
    if capability == "sensorState":
        return str(value).lower() == "alarm"
    return None


def is_motion(value: Any) -> bool:
    """Sim motionSensor sends bools; the Altice camera sends "" or a non-empty marker."""
    if isinstance(value, bool):
        return value
    return value not in (None, "", 0, "0") and str(value).lower() not in ("false", "none")


async def entrance_door_ids(registry: DeviceRegistry, rooms: list[Room]) -> set[str]:
    entrance = {r.id for r in rooms if r.isEntrance}
    return {
        d.id
        for d in await registry.list_devices()
        if d.enabled and d.type == "contactSensor" and d.roomId in entrance
    }


async def compute_presence_facts(
    registry: DeviceRegistry, repo: Repository, empty_after_seconds: float, lookback_hours: float = 12
) -> PresenceFacts:
    now = datetime.now(UTC)
    rooms = await repo.list_rooms()
    doors = await entrance_door_ids(registry, rooms)
    devices = {d.id: d for d in await registry.list_devices()}
    changes = await repo.changes_between(now - timedelta(hours=lookback_hours), now)

    last_door: DoorEvent | None = None
    last_motion: dict[str, RoomMotion] = {}
    for c in changes:  # oldest first
        dev = devices.get(c.deviceId)
        if c.deviceId in doors and c.capability in DOOR_CAPABILITIES:
            opened = door_is_open(c.capability, c.new)
            if opened:
                last_door = DoorEvent(deviceId=c.deviceId, roomId=dev.roomId if dev else None,
                                      openedAt=c.at)
            elif opened is False and last_door is not None and last_door.closedAt is None:
                last_door.closedAt = c.at
        elif c.capability == MOTION_CAPABILITY and dev is not None and is_motion(c.new):
            last_motion[c.deviceId] = RoomMotion(roomId=dev.roomId, deviceId=c.deviceId, at=c.at)

    motions = sorted(last_motion.values(), key=lambda m: m.at)
    door_at = (last_door.closedAt or last_door.openedAt) if last_door else None
    return PresenceFacts(
        now=now,
        lastDoorEvent=last_door,
        lastMotionByRoom=motions,
        secondsSinceLastMotion=(now - motions[-1].at).total_seconds() if motions else None,
        secondsSinceLastDoorEvent=(now - door_at).total_seconds() if door_at else None,
        motionAfterDoorEvent=bool(door_at and any(m.at > door_at for m in motions)),
        emptyAfterSeconds=empty_after_seconds,
    )


class PresenceWatcher:
    """Poller hook. After an entrance-door event, wake the agent once `emptyAfterSeconds` have
    passed: in an empty house nothing else changes, so nothing else would wake it. The agent
    still decides from `get_presence_facts()`."""

    def __init__(
        self,
        registry: DeviceRegistry,
        repo: Repository,
        empty_after_seconds: float,
        wake: Callable[[str], Awaitable[None]],
    ) -> None:
        self._registry = registry
        self._repo = repo
        self._delay = empty_after_seconds
        self._wake = wake
        self._timer: asyncio.Task[None] | None = None

    async def on_changes(self, changes: list[StateChange]) -> list[StateChange]:
        door_changes = [c for c in changes if c.capability in DOOR_CAPABILITIES]
        if not door_changes:
            return []
        doors = await entrance_door_ids(self._registry, await self._repo.list_rooms())
        if any(c.deviceId in doors for c in door_changes):
            self.arm()
        return []

    def arm(self) -> None:
        self.cancel()
        self._timer = asyncio.create_task(self._fire())

    def cancel(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()

    async def _fire(self) -> None:
        await asyncio.sleep(self._delay + 1)
        await self._wake(
            f"{self._delay:.0f}s have passed since the last entrance-door event. "
            "Call get_presence_facts() and apply the empty-house rule."
        )


# --- routines (S3): aggregated facts only, the agent decides what is a routine --------------


def is_active(value: Any, threshold: float) -> bool:
    """power "on"/True, bool True, or a numeric reading above `threshold` (e.g. watts)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value > threshold
    return str(value).lower() in ("on", "true")


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _minutes(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    active = [d for d in days if d["firstOn"]]
    if not active:
        return {"days": len(days), "activeDays": 0}
    starts = sorted(_minutes(d["firstOn"]) for d in active)
    durations = sorted(d["durationMin"] for d in active if d["durationMin"] is not None)

    def fmt(m: float) -> str:
        return f"{int(m) // 60:02d}:{int(m) % 60:02d}"

    return {
        "days": len(days),
        "activeDays": len(active),
        "medianFirstOn": fmt(statistics.median(starts)),
        "earliestFirstOn": fmt(starts[0]),
        "latestFirstOn": fmt(starts[-1]),
        "spreadMin": starts[-1] - starts[0],
        "medianDurationMin": round(statistics.median(durations), 1) if durations else None,
    }


def routine_stats(
    changes: list[StateChange], device_id: str, capability: str, days: int, threshold: float,
    today: datetime,
) -> dict[str, Any]:
    """Per day: first activation time and how long it stayed active. Times are UTC."""
    first_day = (today - timedelta(days=days - 1)).date()
    by_day: dict[Any, dict[str, Any]] = {}
    for i in range(days):
        d = first_day + timedelta(days=i)
        by_day[d] = {"date": d.isoformat(), "weekday": d.weekday(), "firstOn": None,
                     "durationMin": None, "activations": 0}
    active_since: datetime | None = None
    was_active = False
    for c in changes:  # oldest first
        now_active = is_active(c.new, threshold)
        day = by_day.get(c.at.date())
        if now_active and not was_active:
            active_since = c.at
            if day is not None:
                day["activations"] += 1
                if day["firstOn"] is None:
                    day["firstOn"] = _hm(c.at)
        elif was_active and not now_active and active_since is not None:
            start_day = by_day.get(active_since.date())
            if start_day is not None and start_day["firstOn"] == _hm(active_since):
                start_day["durationMin"] = round((c.at - active_since).total_seconds() / 60, 1)
            active_since = None
        was_active = now_active
    rows = list(by_day.values())
    return {
        "deviceId": device_id,
        "capability": capability,
        "threshold": threshold,
        "timezone": "UTC",
        "weekdays": _summary([r for r in rows if r["weekday"] < 5]),
        "weekends": _summary([r for r in rows if r["weekday"] >= 5]),
        "byDay": rows,
    }


async def compute_routine_stats(
    repo: Repository, device_id: str, capability: str, days: int = 14, threshold: float = 20
) -> dict[str, Any]:
    today = datetime.now(UTC)
    since = datetime.combine((today - timedelta(days=days)).date(), datetime.min.time(), UTC)
    changes = await repo.changes_between(since, today, device_id=device_id, capability=capability)
    return routine_stats(changes, device_id, capability, days, threshold, today)

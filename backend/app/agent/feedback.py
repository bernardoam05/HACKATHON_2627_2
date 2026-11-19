"""User feedback, manual-override detection and device cooldowns (D9).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas import (
    ActionRecord,
    ActionStatus,
    ActionUpdatedMsg,
    CooldownEndedMsg,
    CooldownReason,
    CooldownStartedMsg,
    CooldownUpdate,
    DeviceCooldown,
    StateChange,
    UserFeedback,
)
from app.storage.repository import Repository

from .insights import InsightEngine

log = logging.getLogger(__name__)

FeedbackKind = Literal["undo", "confirm", "reject", "manual_override"]
Broadcast = Callable[[BaseModel], Awaitable[None]]
DeviceCheck = Callable[[str], Awaitable[Any]]  # raises KeyError for unknown devices


def _fmt(value: Any) -> str:
    return "?" if value is None else str(value)


def _minutes_text(minutes: float) -> str:
    return f"{minutes:g} min" if minutes < 60 else f"{minutes / 60:g} h"


class FeedbackService:
    """Record undo/confirm/reject/manual_override; cooldown per device. Implements CooldownService.

    After a user override or Undo the device is blocked for the agent and reflexes. The user is
    told (WS `cooldown_started` with a PT-PT note) and can shorten, extend, make it indefinite,
    unblock, or change the default for that device (`rememberForDevice`; 0 = never block).
    """

    def __init__(
        self,
        repo: Repository,
        insights: InsightEngine | None = None,
        broadcast: Broadcast | None = None,
        default_minutes: float = 20,
        override_window_minutes: float = 10,
        device_check: DeviceCheck | None = None,
    ) -> None:
        self._repo = repo
        self._insights = insights
        self._broadcast = broadcast
        self._default_minutes = default_minutes
        self._window = timedelta(minutes=override_window_minutes)
        self._device_check = device_check
        self._cooldowns: dict[str, DeviceCooldown] = {}
        self._defaults: dict[str, float] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._overridden: set[str] = set()  # action ids already reported as overridden

    async def load(self) -> None:
        """Restore cooldowns and per-device defaults after a restart."""
        self._defaults = await self._repo.cooldown_defaults()
        now = datetime.now(UTC)
        for c in await self._repo.list_cooldowns():
            if c.until is not None and c.until <= now:
                await self._repo.delete_cooldown(c.deviceId)
                continue
            self._cooldowns[c.deviceId] = c
            self._arm_timer(c)

    def close(self) -> None:
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()

    # --- queries used by the agent / executor -----------------------------------------------

    def in_cooldown(self, device_id: str, now: datetime | None = None) -> bool:
        c = self._cooldowns.get(device_id)
        if c is None:
            return False
        return c.until is None or (now or datetime.now(UTC)) < c.until

    def cooldowns(self) -> dict[str, datetime | None]:
        """Active blocks: deviceId -> until (None = until the user unblocks)."""
        return {c.deviceId: c.until for c in self._active()}

    def default_minutes(self, device_id: str) -> float:
        return self._defaults.get(device_id, self._default_minutes)

    def _active(self) -> list[DeviceCooldown]:
        return [c for c in self._cooldowns.values() if self.in_cooldown(c.deviceId)]

    # --- feedback ---------------------------------------------------------------------------

    async def record(
        self,
        action: ActionRecord,
        kind: FeedbackKind,
        note: str | None = None,
        user_value: Any = None,
    ) -> None:
        now = datetime.now(UTC)
        await self._repo.add_feedback(UserFeedback(actionId=action.id, kind=kind, at=now, note=note))
        if kind in ("undo", "manual_override"):
            await self._start(action, CooldownReason(kind), user_value)
        if self._insights is None:
            return
        if kind == "confirm":
            await self._insights.on_positive(action)
        else:
            await self._insights.on_negative(action, kind)

    async def _start(self, action: ActionRecord, reason: CooldownReason, user_value: Any) -> None:
        minutes = self.default_minutes(action.deviceId)
        cap = action.update.type
        agent_value = _fmt(action.expectedValue)
        if reason == CooldownReason.manual_override:
            what = f"Mudei {cap} para {agent_value}, mas alteraste para {_fmt(user_value)}."
        else:
            what = f"Anulaste a minha ação ({cap} = {agent_value})."
        if minutes <= 0:
            log.info("override on %s logged; device set to never block", action.deviceId)
            return
        cooldown = DeviceCooldown(
            deviceId=action.deviceId,
            until=datetime.now(UTC) + timedelta(minutes=minutes),
            reason=reason,
            actionId=action.id,
            capability=cap,
            note=f"{what} Não mexo neste dispositivo durante {_minutes_text(minutes)}.",
            startedAt=datetime.now(UTC),
            defaultMinutes=minutes,
        )
        await self._set(cooldown)

    # --- CooldownService --------------------------------------------------------------------

    async def list_cooldowns(self) -> list[DeviceCooldown]:
        return sorted(self._active(), key=lambda c: c.startedAt)

    async def update_cooldown(self, device_id: str, body: CooldownUpdate) -> DeviceCooldown:
        if self._device_check is not None:
            await self._device_check(device_id)  # KeyError -> 404
        now = datetime.now(UTC)
        if body.rememberForDevice and body.minutes is not None:
            self._defaults[device_id] = body.minutes
            await self._repo.set_cooldown_default(device_id, body.minutes)
        current = self._cooldowns.get(device_id) if self.in_cooldown(device_id) else None
        base = current or DeviceCooldown(
            deviceId=device_id, reason=CooldownReason.user, startedAt=now,
            note="Bloqueado por ti.", defaultMinutes=self.default_minutes(device_id),
        )
        if body.minutes == 0:
            await self.clear_cooldown(device_id)
            return base.model_copy(update={"until": now,
                                           "defaultMinutes": self.default_minutes(device_id)})
        until = None if body.untilUnblocked else now + timedelta(minutes=body.minutes or 0)
        updated = base.model_copy(
            update={"until": until, "defaultMinutes": self.default_minutes(device_id)}
        )
        await self._set(updated)
        return updated

    async def clear_cooldown(self, device_id: str) -> None:
        existed = self.in_cooldown(device_id)
        await self._remove(device_id)
        if existed:
            await self._emit(CooldownEndedMsg(deviceId=device_id, reason="user"))

    # --- internals --------------------------------------------------------------------------

    async def _set(self, cooldown: DeviceCooldown) -> None:
        self._cooldowns[cooldown.deviceId] = cooldown
        await self._repo.save_cooldown(cooldown)
        self._arm_timer(cooldown)
        log.info("cooldown on %s until %s (%s)", cooldown.deviceId,
                 cooldown.until or "unblocked by user", cooldown.reason.value)
        await self._emit(CooldownStartedMsg(cooldown=cooldown))

    async def _remove(self, device_id: str) -> None:
        self._cooldowns.pop(device_id, None)
        timer = self._timers.pop(device_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        await self._repo.delete_cooldown(device_id)

    def _arm_timer(self, cooldown: DeviceCooldown) -> None:
        old = self._timers.pop(cooldown.deviceId, None)
        if old is not None:
            old.cancel()
        if cooldown.until is None:
            return
        with contextlib.suppress(RuntimeError):  # no running loop (sync callers in tests)
            self._timers[cooldown.deviceId] = asyncio.create_task(self._expire(cooldown))

    async def _expire(self, cooldown: DeviceCooldown) -> None:
        assert cooldown.until is not None
        await asyncio.sleep(max(0.0, (cooldown.until - datetime.now(UTC)).total_seconds()))
        if self._cooldowns.get(cooldown.deviceId) is not cooldown:
            return
        await self._remove(cooldown.deviceId)
        await self._emit(CooldownEndedMsg(deviceId=cooldown.deviceId, reason="expired"))

    async def _emit(self, msg: BaseModel) -> None:
        if self._broadcast is not None:
            await self._broadcast(msg)

    # --- override detection -----------------------------------------------------------------

    async def on_changes(self, changes: list[StateChange]) -> list[StateChange]:
        """Poller hook: detect a user reverting/overriding a recent agent action (D9)."""
        user_changes = [c for c in changes if not c.causedByAgent and not c.capability.startswith("_")]
        if not user_changes:
            return []
        executed = await self._recent_executed()
        for c in user_changes:
            await self._check_override(executed, c.deviceId, c.capability, c.new, c.at, "")
        return []

    async def on_manual(self, device_id: str, capability: str, value: Any) -> None:
        """Manual control from the app: checked right away, so a quick back-and-forth between
        two polls is not missed."""
        executed = await self._recent_executed()
        await self._check_override(
            executed, device_id, capability, value, datetime.now(UTC), " (via app)"
        )

    async def _recent_executed(self) -> list[ActionRecord]:
        actions = await self._repo.actions_since(datetime.now(UTC) - self._window)
        return [a for a in actions if a.status == ActionStatus.executed]

    async def _check_override(
        self,
        executed: list[ActionRecord],
        device_id: str,
        capability: str,
        value: Any,
        at: datetime,
        suffix: str,
    ) -> None:
        match = next(
            (
                a for a in reversed(executed)
                if a.deviceId == device_id and a.update.type == capability
                and a.createdAt <= at and a.id not in self._overridden
            ),
            None,
        )
        if match is None or value == match.expectedValue:
            return
        self._overridden.add(match.id)
        log.info("agent action %s on %s overridden by the user%s", match.id, device_id, suffix)
        # The user reverted it: no longer an executed agent action, so anti-loop stops matching
        # it (a value between the old one and the agent's target is not a fade any more) and the
        # UI no longer offers Undo. Saved before the cooldown so the UI gets both in order.
        reverted = match.model_copy(
            update={"status": ActionStatus.undone, "resolvedAt": datetime.now(UTC)}
        )
        await self._repo.save_action(reverted)
        await self._emit(ActionUpdatedMsg(action=reverted))
        await self.record(match, "manual_override", note=f"{capability} -> {value!r}{suffix}",
                          user_value=value)

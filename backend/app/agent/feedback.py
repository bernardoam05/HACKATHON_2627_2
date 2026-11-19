"""User feedback and manual-override detection (D9).

# Owner: Team A
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas import ActionRecord, ActionStatus, ActionUpdatedMsg, StateChange, UserFeedback
from app.storage.repository import Repository

from .insights import InsightEngine

log = logging.getLogger(__name__)

OVERRIDE_WINDOW = timedelta(minutes=10)  # a user change this soon after an agent action reverts it
COOLDOWN = timedelta(minutes=20)  # the agent leaves the device alone this long

FeedbackKind = Literal["undo", "confirm", "reject", "manual_override"]


class FeedbackService:
    """Record undo/confirm/reject/manual_override; cooldown per device."""

    def __init__(
        self,
        repo: Repository,
        insights: InsightEngine | None = None,
        broadcast: Callable[[BaseModel], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repo
        self._insights = insights
        self._broadcast = broadcast
        self._cooldown_until: dict[str, datetime] = {}
        self._overridden: set[str] = set()  # action ids already reported as overridden

    def in_cooldown(self, device_id: str, now: datetime | None = None) -> bool:
        until = self._cooldown_until.get(device_id)
        return until is not None and (now or datetime.now(UTC)) < until

    def cooldowns(self) -> dict[str, datetime]:
        now = datetime.now(UTC)
        return {d: u for d, u in self._cooldown_until.items() if u > now}

    async def record(self, action: ActionRecord, kind: FeedbackKind, note: str | None = None) -> None:
        now = datetime.now(UTC)
        await self._repo.add_feedback(UserFeedback(actionId=action.id, kind=kind, at=now, note=note))
        if kind in ("undo", "manual_override"):
            self._cooldown_until[action.deviceId] = now + COOLDOWN
        if self._insights is None:
            return
        if kind == "confirm":
            await self._insights.on_positive(action)
        else:
            await self._insights.on_negative(action, kind)

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
        actions = await self._repo.actions_since(datetime.now(UTC) - OVERRIDE_WINDOW)
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
        log.info("manual override of action %s on %s%s", match.id, device_id, suffix)
        await self.record(match, "manual_override", note=f"{capability} -> {value!r}{suffix}")
        # The user reverted it: no longer an executed agent action, so anti-loop stops matching
        # it (a value between the old one and the agent's target is not a fade any more) and the
        # UI no longer offers Undo.
        reverted = match.model_copy(
            update={"status": ActionStatus.undone, "resolvedAt": datetime.now(UTC)}
        )
        await self._repo.save_action(reverted)
        if self._broadcast is not None:
            await self._broadcast(ActionUpdatedMsg(action=reverted))

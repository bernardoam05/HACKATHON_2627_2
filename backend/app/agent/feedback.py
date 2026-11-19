"""User feedback and manual-override detection (D9).

# Owner: Team A
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.schemas import ActionRecord, ActionStatus, StateChange, UserFeedback
from app.storage.repository import Repository

from .insights import InsightEngine

log = logging.getLogger(__name__)

OVERRIDE_WINDOW = timedelta(minutes=10)  # a user change this soon after an agent action reverts it
COOLDOWN = timedelta(minutes=20)  # the agent leaves the device alone this long

FeedbackKind = Literal["undo", "confirm", "reject", "manual_override"]


class FeedbackService:
    """Record undo/confirm/reject/manual_override; cooldown per device."""

    def __init__(self, repo: Repository, insights: InsightEngine | None = None) -> None:
        self._repo = repo
        self._insights = insights
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
        now = datetime.now(UTC)
        actions = await self._repo.actions_since(now - OVERRIDE_WINDOW)
        executed = [a for a in actions if a.status == ActionStatus.executed]
        for c in user_changes:
            match = next(
                (
                    a for a in reversed(executed)
                    if a.deviceId == c.deviceId and a.update.type == c.capability
                    and a.createdAt <= c.at and a.id not in self._overridden
                ),
                None,
            )
            if match is None or c.new == match.expectedValue:
                continue
            self._overridden.add(match.id)
            log.info("manual override of action %s on %s", match.id, c.deviceId)
            await self.record(match, "manual_override", note=f"{c.capability} -> {c.new!r}")
        return []

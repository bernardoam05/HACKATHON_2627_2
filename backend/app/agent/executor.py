"""Executor: applies autonomy rules and writes via the device layer (D6, D10).

# Owner: Team A
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.devices.registry import DeviceRegistry
from app.plugins.registry import PluginRegistry
from app.schemas import (
    ActionRecord,
    ActionStatus,
    ActionUpdatedMsg,
    AutonomyLevel,
    CapabilityUpdate,
    Decision,
    Device,
    ProposedAction,
    UserFeedback,
)
from app.storage.repository import Repository

from .feedback import FeedbackKind, FeedbackService

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]


SECURITY_STATUS = "securityStatus"
SECURITY_STATUS_VALUES = ("disarm", "stay", "away")


class InvalidAction(ValueError):
    pass


class Executor:
    """execute(Decision): validate against plugin, resolve autonomy, auto/auto_notify execute, suggest -> pending_confirmation, honour override cooldown. Implements ActionService confirm/reject/undo.

    The alarm is driven through the pseudo-capability `securityStatus` on the gateway
    (`disarm` | `stay` | `away`), always `suggest`, written with `patch_home_security_status`.
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        repo: Repository,
        plugins: PluginRegistry,
        broadcast: Broadcast,
        feedback: FeedbackService | None = None,
    ) -> None:
        self._registry = registry
        self._repo = repo
        self._plugins = plugins
        self._broadcast = broadcast
        self._feedback_svc = feedback

    # --- applying decisions -----------------------------------------------------------------

    async def execute(
        self, decision: Decision, respect_cooldown: bool = True
    ) -> list[ActionRecord]:
        """`respect_cooldown=False` for explicit user commands from chat."""
        records: list[ActionRecord] = []
        for proposed in decision.actions:
            record = await self.apply(decision.id, proposed, respect_cooldown=respect_cooldown)
            if record is not None:
                records.append(record)
        return records

    async def apply(
        self,
        decision_id: str,
        proposed: ProposedAction,
        never_suggest: bool = False,
        respect_cooldown: bool = True,
    ) -> ActionRecord | None:
        """Validate and apply one action. Returns None when it is a no-op (value already set),
        when the device is in an override cooldown (D9) or, with `never_suggest` (reflexes, D16),
        when it would need confirmation."""
        try:
            device = await self._registry.get_device(proposed.deviceId)
        except KeyError:
            log.warning("decision %s: unknown device %s", decision_id, proposed.deviceId)
            return await self._record(decision_id, proposed.deviceId, proposed.update,
                                      AutonomyLevel.suggest, ActionStatus.failed)
        if respect_cooldown and self._feedback_svc and self._feedback_svc.in_cooldown(device.id):
            log.info("decision %s: %s is in override cooldown, skipped", decision_id, device.id)
            return None
        try:
            update = await self.validate(device, proposed.update)
        except InvalidAction as e:
            log.warning("decision %s: rejected invalid action: %s", decision_id, e)
            return await self._record(decision_id, device.id, proposed.update,
                                      AutonomyLevel.suggest, ActionStatus.failed)

        current = device.capability(update.type)
        if current is not None and current.value == update.value:
            return None

        autonomy = await self._plugins.resolve_autonomy(device, update.type)
        if autonomy == AutonomyLevel.suggest:
            if never_suggest:
                return None
            return await self._record(decision_id, device.id, update, autonomy,
                                      ActionStatus.pending_confirmation,
                                      prev=current.value if current else None)
        return await self._run(decision_id, device, update, autonomy)

    async def validate(self, device: Device, update: CapabilityUpdate) -> CapabilityUpdate:
        """Never trust the LLM: capability must exist and be writable; values within range."""
        if not device.enabled:
            raise InvalidAction(f"{device.id} is disabled")
        if update.type == SECURITY_STATUS:
            if device.type != "gateway" or update.value not in SECURITY_STATUS_VALUES:
                raise InvalidAction(f"securityStatus must be one of {SECURITY_STATUS_VALUES} on a gateway")
            return update
        cap = device.capability(update.type)
        if cap is None:
            raise InvalidAction(f"{device.id} has no capability {update.type!r}")
        plugin = await self._plugins.get(device.type)
        if plugin is not None:
            spec = next((s for s in plugin.capabilities if s.type == update.type), None)
            if spec is not None and not spec.writable:
                raise InvalidAction(f"{device.type}.{update.type} is read-only")
        value = update.value
        if cap.availableValues and value not in cap.availableValues:
            raise InvalidAction(f"{value!r} not in {cap.availableValues} for {update.type}")
        if isinstance(value, int | float) and not isinstance(value, bool):
            lo = cap.min if cap.min is not None else value
            hi = cap.max if cap.max is not None else value
            clamped = min(max(value, lo), hi)
            if clamped != value:
                update = update.model_copy(update={"value": clamped})
        return update

    async def _run(
        self, decision_id: str, device: Device, update: CapabilityUpdate, autonomy: AutonomyLevel
    ) -> ActionRecord:
        current = device.capability(update.type)
        # Saved BEFORE writing so the poller's anti-loop already sees it (D8).
        record = await self._record(decision_id, device.id, update, autonomy, ActionStatus.executed,
                                    prev=current.value if current else None)
        try:
            await self._registry.write(device.id, update)
        except Exception as e:
            log.warning("write %s %s failed: %r", device.id, update.type, e)
            record = record.model_copy(
                update={"status": ActionStatus.failed, "resolvedAt": _now()}
            )
            await self._save(record)
        return record

    async def _record(
        self,
        decision_id: str,
        device_id: str,
        update: CapabilityUpdate,
        autonomy: AutonomyLevel,
        status: ActionStatus,
        prev: Any = None,
    ) -> ActionRecord:
        record = ActionRecord(
            id=uuid.uuid4().hex,
            decisionId=decision_id,
            deviceId=device_id,
            update=update,
            autonomy=autonomy,
            status=status,
            expectedValue=update.value,
            createdAt=_now(),
            resolvedAt=_now() if status == ActionStatus.failed else None,
        )
        await self._repo.save_action(record, prev_value=prev)
        await self._broadcast(ActionUpdatedMsg(action=record))
        return record

    async def _save(self, record: ActionRecord) -> None:
        await self._repo.save_action(record)
        await self._broadcast(ActionUpdatedMsg(action=record))

    # --- ActionService ----------------------------------------------------------------------

    async def list_decisions(self, limit: int) -> list[Decision]:
        return await self._repo.list_decisions(limit)

    async def list_actions(self, status: str | None, limit: int) -> list[ActionRecord]:
        return await self._repo.list_actions(status, limit)

    async def _get(self, action_id: str, expected: ActionStatus) -> ActionRecord:
        record = await self._repo.get_action(action_id)
        if record is None:
            raise KeyError(action_id)
        if record.status != expected:
            raise ValueError(f"action {action_id} is {record.status.value}, not {expected.value}")
        return record

    async def confirm(self, action_id: str) -> ActionRecord:
        """User confirmed a suggestion: write it now."""
        record = await self._get(action_id, ActionStatus.pending_confirmation)
        await self._feedback(record, "confirm")
        # Re-stamp createdAt so anti-loop measures from the real write time.
        record = record.model_copy(update={"status": ActionStatus.executed, "createdAt": _now(),
                                           "resolvedAt": _now()})
        await self._save(record)
        try:
            device = await self._registry.get_device(record.deviceId)
            update = await self.validate(device, record.update)
            await self._registry.write(device.id, update)
        except Exception as e:
            log.warning("confirm %s failed: %r", action_id, e)
            record = record.model_copy(update={"status": ActionStatus.failed})
            await self._save(record)
        return record

    async def reject(self, action_id: str) -> ActionRecord:
        record = await self._get(action_id, ActionStatus.pending_confirmation)
        await self._feedback(record, "reject")
        record = record.model_copy(update={"status": ActionStatus.rejected, "resolvedAt": _now()})
        await self._save(record)
        return record

    async def undo(self, action_id: str) -> ActionRecord:
        """Restore the value the capability had before the action."""
        record = await self._get(action_id, ActionStatus.executed)
        known, prev = await self._repo.action_prev_value(action_id)
        if not known or prev is None:
            raise ValueError(f"action {action_id} has no previous value to restore")
        await self._registry.write(record.deviceId, CapabilityUpdate(type=record.update.type, value=prev))
        await self._feedback(record, "undo")
        record = record.model_copy(update={"status": ActionStatus.undone, "resolvedAt": _now()})
        await self._save(record)
        return record

    async def _feedback(self, action: ActionRecord, kind: FeedbackKind) -> None:
        if self._feedback_svc is not None:
            await self._feedback_svc.record(action, kind)
        else:
            await self._repo.add_feedback(UserFeedback(actionId=action.id, kind=kind, at=_now()))


def _now() -> datetime:
    return datetime.now(UTC)

"""History and action contracts. (Shared contract)"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .device import CapabilityUpdate, Device
from .plugin import AutonomyLevel


class StateSnapshot(BaseModel):
    id: str
    takenAt: datetime
    devices: list[Device]


class StateChange(BaseModel):
    id: str
    deviceId: str
    capability: str
    old: Any = None
    new: Any = None
    at: datetime
    causedByAgent: bool = False
    actionId: str | None = None


class ActionStatus(str, Enum):
    executed = "executed"
    pending_confirmation = "pending_confirmation"
    rejected = "rejected"
    undone = "undone"
    failed = "failed"


class ActionRecord(BaseModel):
    id: str
    decisionId: str
    deviceId: str
    update: CapabilityUpdate
    autonomy: AutonomyLevel
    status: ActionStatus
    expectedValue: Any = None
    createdAt: datetime
    resolvedAt: datetime | None = None


class UserFeedback(BaseModel):
    actionId: str
    kind: Literal["undo", "confirm", "reject", "manual_override"]
    at: datetime
    note: str | None = None


class CooldownReason(str, Enum):
    manual_override = "manual_override"  # user changed the device against a recent agent action
    undo = "undo"  # user pressed Undo on an agent action
    user = "user"  # user blocked the device from the UI


class DeviceCooldown(BaseModel):
    """The agent (and reflexes) leave this device alone until `until` (D9).

    `until = None` means blocked until the user unblocks it.
    """

    deviceId: str
    until: datetime | None = None
    reason: CooldownReason
    actionId: str | None = None  # the overridden / undone agent action
    capability: str | None = None
    note: str | None = None  # user-facing (PT-PT), e.g. what the agent did and what the user set
    startedAt: datetime
    defaultMinutes: float  # duration used for this device's next override (0 = never block)


class CooldownUpdate(BaseModel):
    """User choice from the UI. Give `minutes` (0 = unblock now) or `untilUnblocked`.
    `rememberForDevice` makes `minutes` the default for future overrides on this device."""

    minutes: float | None = Field(default=None, ge=0, le=24 * 60)
    untilUnblocked: bool = False
    rememberForDevice: bool = False

    @model_validator(mode="after")
    def _one_choice(self) -> CooldownUpdate:
        if (self.minutes is None) == (not self.untilUnblocked):
            raise ValueError("give exactly one of `minutes` or `untilUnblocked: true`")
        if self.untilUnblocked and self.rememberForDevice:
            raise ValueError("`rememberForDevice` needs `minutes`")
        return self

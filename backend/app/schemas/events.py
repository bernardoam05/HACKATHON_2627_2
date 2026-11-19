"""History and action contracts. (Shared contract)"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel

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

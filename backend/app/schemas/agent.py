"""Agent contracts. (Shared contract)"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .device import CapabilityUpdate, Room
from .events import ActionRecord, StateChange, StateSnapshot, UserFeedback
from .plugin import DevicePlugin


class ProposedAction(BaseModel):
    deviceId: str
    update: CapabilityUpdate
    reason: str = ""


class InsightStatus(str, Enum):
    active = "active"
    dropped = "dropped"


class Insight(BaseModel):
    id: str
    statement: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = []
    createdAt: datetime
    lastConfirmedAt: datetime
    status: InsightStatus = InsightStatus.active


class InsightUpdate(BaseModel):
    op: Literal["create", "reinforce", "weaken", "delete"]
    insightId: str | None = None
    statement: str | None = None
    delta: float | None = None
    evidence: list[str] | None = None


class TimeWindow(BaseModel):
    from_: str = Field(alias="from", description="HH:MM")
    to: str = Field(description="HH:MM")
    weekdays: list[int] | None = None

    model_config = {"populate_by_name": True}


class ReflexTrigger(BaseModel):
    deviceId: str
    capability: str
    operator: Literal["=", "<", ">", "changed"]
    value: float | str | bool | None = None
    roomId: str | None = None
    timeWindow: TimeWindow | None = None


class ReflexStatus(str, Enum):
    proposed = "proposed"
    active = "active"
    paused = "paused"
    deleted = "deleted"


class Reflex(BaseModel):
    id: str
    name: str
    triggers: list[ReflexTrigger]
    triggerLogic: Literal["all", "any"] = "all"
    actions: list[ProposedAction]
    sourceInsightId: str | None = None
    status: ReflexStatus = ReflexStatus.proposed
    createdAt: datetime
    executions: int = 0
    undos: int = 0


class ReflexProposal(BaseModel):
    reflex: Reflex
    rationale: str = ""


class ReflexUpdate(BaseModel):
    reflexId: str
    op: Literal["pause", "resume", "delete", "modify"]
    reflex: Reflex | None = None
    reason: str = ""


class DecisionTrigger(str, Enum):
    state_change = "state_change"
    chat = "chat"
    startup = "startup"
    reflex_report = "reflex_report"


class Decision(BaseModel):
    id: str
    createdAt: datetime
    trigger: DecisionTrigger
    explanation: str = Field(description="User-facing, PT-PT")
    inferredRoom: str | None = None
    inferredActivity: str | None = None
    actions: list[ProposedAction] = []
    confidence: float = Field(default=0.5, ge=0, le=1)
    beliefState: str = Field(default="", description="Agent-written summary, <= ~800 chars")
    insightUpdates: list[InsightUpdate] = []
    reflexProposals: list[ReflexProposal] = []
    reflexUpdates: list[ReflexUpdate] = []


class AgentRunInput(BaseModel):
    mode: Literal["delta", "full"]
    snapshot: StateSnapshot | None = None
    changesSinceLastRun: list[StateChange] = []
    beliefState: str | None = None
    insights: list[Insight] = []
    recentActions: list[ActionRecord] = []
    feedback: list[UserFeedback] = []
    rooms: list[Room] = []
    plugins: list[DevicePlugin] = []
    now: datetime


class ChatRequest(BaseModel):
    message: str
    inputMode: Literal["text", "voice"] = "text"


class ChatResponse(BaseModel):
    reply: str
    decision: Decision | None = None


class PluginProposal(BaseModel):
    plugin: DevicePlugin
    rationale: str = ""


class DoorEvent(BaseModel):
    deviceId: str
    roomId: str | None = None
    openedAt: datetime
    closedAt: datetime | None = None


class RoomMotion(BaseModel):
    roomId: str | None = None
    deviceId: str
    at: datetime


class PresenceFacts(BaseModel):
    """Raw facts only. No `isEmpty` field on purpose (D14)."""

    now: datetime
    lastDoorEvent: DoorEvent | None = None
    lastMotionByRoom: list[RoomMotion] = []
    secondsSinceLastMotion: float | None = None
    secondsSinceLastDoorEvent: float | None = None
    motionAfterDoorEvent: bool = False
    emptyAfterSeconds: float

"""Agent contracts. (Shared contract)"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ReflexTrigger(BaseModel):
    """`state`: a device change (deviceId + capability + operator/value).
    `time`: every day at `at` (house time, HH:MM), optionally only on `weekdays` (0 = Monday).
    `first_motion`: `delayMinutes` after the first motion of the day, optionally on `weekdays`.
    In an `all` reflex, `state` triggers next to a time trigger act as conditions on the
    current state (e.g. "07:30, only if the bedroom light is off")."""

    kind: Literal["state", "time", "first_motion"] = "state"
    deviceId: str = ""  # "" for time/first_motion triggers (kept a string for old clients)
    capability: str = ""
    operator: Literal["=", "<", ">", "changed"] = "="
    value: float | str | bool | None = None
    roomId: str | None = None
    timeWindow: TimeWindow | None = None
    at: str | None = Field(default=None, description="HH:MM, house time (kind=time)")
    weekdays: list[int] | None = Field(default=None, description="0 = Monday (time/first_motion)")
    delayMinutes: float | None = Field(default=None, ge=0, le=720, description="kind=first_motion")

    @model_validator(mode="after")
    def _check_kind(self) -> ReflexTrigger:
        if self.kind == "state" and (not self.deviceId or not self.capability):
            raise ValueError("a state trigger needs deviceId and capability")
        if self.kind == "time" and not (self.at and _HHMM.match(self.at)):
            raise ValueError("a time trigger needs `at` as HH:MM")
        if self.weekdays is not None and any(d not in range(7) for d in self.weekdays):
            raise ValueError("weekdays are 0 (Monday) .. 6 (Sunday)")
        return self


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
    lastFiredAt: datetime | None = None  # scheduled reflexes fire at most once per house day
    # Scheduled reflexes only: at the due time, wake the agent to decide whether the actions
    # still make sense (is anyone home?) instead of running them blindly (costs one LLM call).
    askAgent: bool = False
    # "user": created by the user (POST /api/reflexes); the agent may only pause it.
    createdBy: Literal["agent", "user"] = "agent"


class ReflexCreate(BaseModel):
    """A schedule/automation created by the user (POST /api/reflexes): active at once.
    Its actions may never need confirmation (D16): `suggest` capabilities are refused (409)."""

    name: str
    triggers: list[ReflexTrigger] = Field(min_length=1)
    triggerLogic: Literal["all", "any"] = "all"
    actions: list[ProposedAction] = Field(min_length=1)
    askAgent: bool = False


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


class FallbackInfo(BaseModel):
    """A model of the fallback chain that failed before another one answered."""

    model: str
    error: str = Field(description="Error class and HTTP status, e.g. 'ModelUnavailableError 503'")
    ms: int = Field(ge=0, description="Time lost on this attempt")


class RunInfo(BaseModel):
    """Telemetry of the agent run that produced a Decision."""

    durationMs: int = Field(ge=0, description="Wall time of the LLM run (prompt sent -> answer)")
    model: str | None = Field(default=None, description="Model(s) that answered, comma-separated")
    tier: Literal["fast", "reasoning"]
    mode: Literal["delta", "full"]
    modelRequests: int = Field(default=1, ge=0, description="LLM round-trips (tool loops add more)")
    fallbacks: list[FallbackInfo] = Field(
        default=[], description="Models that failed before the answering one, in order"
    )
    inputTokens: int | None = Field(
        default=None, ge=0, description="Prompt tokens summed over the run's model requests"
    )
    outputTokens: int | None = Field(
        default=None, ge=0, description="Completion tokens (incl. thinking) summed over the run"
    )


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
    runInfo: RunInfo | None = Field(
        default=None, description="Absent on decisions made without an LLM run (and old ones)"
    )


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

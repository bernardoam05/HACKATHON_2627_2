"""WebSocket message contracts (discriminated union on `type`). (Shared contract)"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .agent import Decision, Insight, Reflex
from .device import Device
from .events import ActionRecord, DeviceCooldown, StateChange


class StateChangedMsg(BaseModel):
    type: Literal["state_changed"] = "state_changed"
    changes: list[StateChange]


class DecisionMsg(BaseModel):
    type: Literal["decision"] = "decision"
    decision: Decision


class ActionUpdatedMsg(BaseModel):
    type: Literal["action_updated"] = "action_updated"
    action: ActionRecord


class AgentStatusMsg(BaseModel):
    type: Literal["agent_status"] = "agent_status"
    busy: bool
    dirty: bool


class DeviceAddedMsg(BaseModel):
    type: Literal["device_added"] = "device_added"
    device: Device


class DeviceRemovedMsg(BaseModel):
    type: Literal["device_removed"] = "device_removed"
    device: Device


class PluginNeededMsg(BaseModel):
    type: Literal["plugin_needed"] = "plugin_needed"
    deviceType: str


class InsightUpdatedMsg(BaseModel):
    type: Literal["insight_updated"] = "insight_updated"
    insight: Insight


class ReflexProposedMsg(BaseModel):
    type: Literal["reflex_proposed"] = "reflex_proposed"
    reflex: Reflex


class ReflexUpdatedMsg(BaseModel):
    type: Literal["reflex_updated"] = "reflex_updated"
    reflex: Reflex


class ReflexExecutedMsg(BaseModel):
    type: Literal["reflex_executed"] = "reflex_executed"
    reflexId: str
    actions: list[ActionRecord]


class CooldownStartedMsg(BaseModel):
    """The user overrode the agent: show the note and the blocking options (PUT/DELETE
    /api/cooldowns/{deviceId})."""

    type: Literal["cooldown_started"] = "cooldown_started"
    cooldown: DeviceCooldown


class CooldownEndedMsg(BaseModel):
    type: Literal["cooldown_ended"] = "cooldown_ended"
    deviceId: str
    reason: Literal["expired", "user"]


WsMessage = Annotated[
    StateChangedMsg
    | DecisionMsg
    | ActionUpdatedMsg
    | AgentStatusMsg
    | DeviceAddedMsg
    | DeviceRemovedMsg
    | PluginNeededMsg
    | InsightUpdatedMsg
    | ReflexProposedMsg
    | ReflexUpdatedMsg
    | ReflexExecutedMsg
    | CooldownStartedMsg
    | CooldownEndedMsg,
    Field(discriminator="type"),
]

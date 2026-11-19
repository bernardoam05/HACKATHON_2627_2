"""Fixture-backed fake services so Team B can build the UI before Team A is ready. (Shared contract)"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import (
    ActionRecord,
    ActionStatus,
    AutonomyLevel,
    CapabilityUpdate,
    ChatRequest,
    ChatResponse,
    CooldownReason,
    CooldownUpdate,
    Decision,
    DecisionTrigger,
    Device,
    DeviceAssignment,
    DeviceCooldown,
    DevicePlugin,
    DevicePurpose,
    DeviceSource,
    Insight,
    PluginProposal,
    PluginStatus,
    ProposedAction,
    Reflex,
    ReflexStatus,
    Room,
    RoomCreate,
    SimDeviceCreate,
    StateChange,
)

from .ports import Services

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# Used only when backend/fixtures/get_home_devices.json is missing.
_FALLBACK: list[dict[str, Any]] = [
    {"id": "bulb-1", "name": "Bulb", "type": "bulb", "categories": ["comfort"],
     "capabilities": [{"type": "power", "value": True},
                      {"type": "brightness", "value": 100, "min": 0, "max": 100}]},
    {"id": "plug-1", "name": "Plug", "type": "plug", "categories": ["energy"],
     "capabilities": [{"type": "power", "value": False}, {"type": "instantPower", "value": 0}]},
]


def _now() -> datetime:
    return datetime.now(UTC)


def _load_devices() -> list[Device]:
    path = FIXTURES / "get_home_devices.json"
    raw = _FALLBACK
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["devices"] if isinstance(data, dict) else data
    return [Device.model_validate({**d, "source": DeviceSource.altice}) for d in raw]


class FakeBackend:
    """Implements every service Protocol in memory."""

    def __init__(self) -> None:
        self._devices = {d.id: d for d in _load_devices()}
        first = next(iter(self._devices))
        self._rooms: dict[str, Room] = {"hack5": Room(id="hack5", name="Hack 5", isEntrance=True)}
        self._decision = Decision(
            id="dec-1", createdAt=_now(), trigger=DecisionTrigger.state_change,
            explanation="Baixei a luz da sala porque ligaste a televisão.",
            inferredRoom="sala", inferredActivity="a ver televisão",
            actions=[ProposedAction(deviceId=first,
                                    update=CapabilityUpdate(type="brightness", value=30),
                                    reason="TV ligada")],
            confidence=0.8, beliefState="Fake: alguém está na sala a ver TV.",
        )
        self._actions: dict[str, ActionRecord] = {
            "act-1": ActionRecord(
                id="act-1", decisionId="dec-1", deviceId=first,
                update=CapabilityUpdate(type="brightness", value=30),
                autonomy=AutonomyLevel.auto_notify, status=ActionStatus.executed,
                expectedValue=30, createdAt=_now()),
        }
        self._insights: dict[str, Insight] = {}
        self._reflexes: dict[str, Reflex] = {}
        # One sample block so the UI can render the override notice and its options.
        self._cooldowns: dict[str, DeviceCooldown] = {
            first: DeviceCooldown(
                deviceId=first, until=_now() + timedelta(minutes=20),
                reason=CooldownReason.manual_override, actionId="act-1", capability="brightness",
                note="Baixei a luz para 30, mas voltaste a pô-la a 90. Não lhe mexo durante 20 min.",
                startedAt=_now(), defaultMinutes=20,
            )
        }
        self._cooldown_defaults: dict[str, float] = {}

    # CooldownService
    async def list_cooldowns(self) -> list[DeviceCooldown]:
        now = _now()
        return [c for c in self._cooldowns.values() if c.until is None or c.until > now]

    async def update_cooldown(self, device_id: str, body: CooldownUpdate) -> DeviceCooldown:
        if device_id not in self._devices:
            raise KeyError(device_id)
        if body.rememberForDevice and body.minutes is not None:
            self._cooldown_defaults[device_id] = body.minutes
        default = self._cooldown_defaults.get(device_id, 20)
        current = self._cooldowns.get(device_id) or DeviceCooldown(
            deviceId=device_id, reason=CooldownReason.user, startedAt=_now(),
            defaultMinutes=default)
        until = None if body.untilUnblocked else _now() + timedelta(minutes=body.minutes or 0)
        updated = current.model_copy(update={"until": until, "defaultMinutes": default})
        if body.minutes == 0:
            self._cooldowns.pop(device_id, None)
        else:
            self._cooldowns[device_id] = updated
        return updated

    async def clear_cooldown(self, device_id: str) -> None:
        self._cooldowns.pop(device_id, None)

    # DeviceService
    async def list_devices(self) -> list[Device]:
        return list(self._devices.values())

    async def get_device(self, device_id: str) -> Device:
        return self._devices[device_id]

    async def assign(self, device_id: str, body: DeviceAssignment) -> Device:
        d = self._devices[device_id]
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(d, k, v)
        return d

    async def set_capability(self, device_id: str, update: CapabilityUpdate) -> Device:
        d = self._devices[device_id]
        cap = d.capability(update.type)
        if cap:
            cap.value = update.value
        return d

    # RoomService
    async def list_rooms(self) -> list[Room]:
        return list(self._rooms.values())

    async def create_room(self, body: RoomCreate) -> Room:
        room = Room(id=uuid.uuid4().hex[:8], **body.model_dump())
        self._rooms[room.id] = room
        return room

    async def delete_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    # SimAdminService
    async def list_templates(self) -> list[dict[str, str]]:
        return [{"id": t, "type": t, "name": t}
                for t in ("tv", "coffee_plug", "bulb", "contactSensor", "motionSensor")]

    async def add_device(self, body: SimDeviceCreate) -> Device:
        d = Device(id=f"sim-{uuid.uuid4().hex[:6]}", name=body.name, type=body.type,
                   capabilities=body.capabilities or [], source=DeviceSource.sim,
                   roomId=body.roomId)
        self._devices[d.id] = d
        return d

    async def remove_device(self, device_id: str) -> None:
        if self._devices[device_id].source == DeviceSource.altice:
            raise ValueError("Altice devices cannot be removed")
        del self._devices[device_id]

    # PluginService
    async def list_plugins(self) -> list[DevicePlugin]:
        return []

    async def unconfigured_types(self) -> list[str]:
        return []

    async def propose(self, type: str) -> PluginProposal:
        return PluginProposal(
            plugin=DevicePlugin(type=type, displayName=type, purpose=DevicePurpose.comfort,
                                status=PluginStatus.proposed),
            rationale="Fake proposal.")

    async def confirm_plugin(self, type: str, plugin: DevicePlugin) -> DevicePlugin:
        return plugin.model_copy(update={"status": PluginStatus.confirmed})

    # AgentService
    async def chat(self, body: ChatRequest) -> ChatResponse:
        return ChatResponse(reply=f"(fake) Recebi: {body.message}", decision=self._decision)

    async def status(self) -> dict[str, Any]:
        return {"busy": False, "dirty": False, "lastRunAt": _now().isoformat(), "model": "fake",
                "beliefState": self._decision.beliefState, "runsSinceFullSnapshot": 0}

    async def list_insights(self) -> list[Insight]:
        return list(self._insights.values())

    async def delete_insight(self, insight_id: str) -> None:
        self._insights.pop(insight_id, None)

    async def list_reflexes(self, status: str | None) -> list[Reflex]:
        return [r for r in self._reflexes.values() if status in (None, r.status.value)]

    async def _set_reflex(self, reflex_id: str, status: ReflexStatus) -> Reflex:
        r = self._reflexes[reflex_id]
        r.status = status
        return r

    async def confirm_reflex(self, reflex_id: str) -> Reflex:
        return await self._set_reflex(reflex_id, ReflexStatus.active)

    async def pause_reflex(self, reflex_id: str) -> Reflex:
        return await self._set_reflex(reflex_id, ReflexStatus.paused)

    async def resume_reflex(self, reflex_id: str) -> Reflex:
        return await self._set_reflex(reflex_id, ReflexStatus.active)

    async def delete_reflex(self, reflex_id: str) -> None:
        self._reflexes.pop(reflex_id, None)

    # ActionService
    async def list_decisions(self, limit: int) -> list[Decision]:
        return [self._decision][:limit]

    async def list_actions(self, status: str | None, limit: int) -> list[ActionRecord]:
        return [a for a in self._actions.values() if status in (None, a.status.value)][:limit]

    async def _resolve(self, action_id: str, status: ActionStatus) -> ActionRecord:
        a = self._actions[action_id]
        a.status, a.resolvedAt = status, _now()
        return a

    async def confirm(self, action_id: str) -> ActionRecord:
        return await self._resolve(action_id, ActionStatus.executed)

    async def reject(self, action_id: str) -> ActionRecord:
        return await self._resolve(action_id, ActionStatus.rejected)

    async def undo(self, action_id: str) -> ActionRecord:
        return await self._resolve(action_id, ActionStatus.undone)

    # HistoryService
    async def list_changes(self, device_id: str | None, since: str | None,
                           limit: int) -> list[StateChange]:
        return []


def build_fake_services() -> Services:
    b = FakeBackend()
    return Services(devices=b, rooms=b, sim=b, plugins=b, agent=b, actions=b, history=b,
                    cooldowns=b)

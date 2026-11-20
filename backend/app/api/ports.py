"""Service Protocols the routes depend on. Implemented by Team A. (Shared contract)"""
from __future__ import annotations

from typing import Any, Protocol

from app.schemas import (
    ActionRecord,
    CapabilityUpdate,
    ChatRequest,
    ChatResponse,
    CooldownUpdate,
    Decision,
    Device,
    DeviceAssignment,
    DeviceCooldown,
    DevicePlugin,
    Insight,
    PluginProposal,
    Reflex,
    ReflexCreate,
    Room,
    RoomCreate,
    SimDeviceCreate,
    StateChange,
)


class DeviceService(Protocol):
    async def list_devices(self) -> list[Device]: ...
    async def get_device(self, device_id: str) -> Device: ...
    async def assign(self, device_id: str, body: DeviceAssignment) -> Device: ...
    async def set_capability(self, device_id: str, update: CapabilityUpdate) -> Device: ...


class RoomService(Protocol):
    async def list_rooms(self) -> list[Room]: ...
    async def create_room(self, body: RoomCreate) -> Room: ...
    async def delete_room(self, room_id: str) -> None: ...


class SimAdminService(Protocol):
    async def list_templates(self) -> list[dict[str, str]]: ...
    async def add_device(self, body: SimDeviceCreate) -> Device: ...
    async def remove_device(self, device_id: str) -> None:
        """Raise ValueError (-> 409) if the device source is `altice`."""
        ...


class PluginService(Protocol):
    async def list_plugins(self) -> list[DevicePlugin]: ...
    async def unconfigured_types(self) -> list[str]: ...
    async def propose(self, type: str) -> PluginProposal: ...
    async def confirm_plugin(self, type: str, plugin: DevicePlugin) -> DevicePlugin: ...


class AgentService(Protocol):
    async def chat(self, body: ChatRequest) -> ChatResponse: ...
    async def status(self) -> dict[str, Any]: ...
    async def list_insights(self) -> list[Insight]: ...
    async def delete_insight(self, insight_id: str) -> None: ...
    async def list_reflexes(self, status: str | None) -> list[Reflex]: ...
    async def create_reflex(self, body: ReflexCreate) -> Reflex:
        """User-created schedule/automation: active at once. ValueError (409) if an action
        would need confirmation (suggest) or targets an unknown capability; KeyError (404) for
        an unknown device."""
        ...
    async def confirm_reflex(self, reflex_id: str) -> Reflex: ...
    async def pause_reflex(self, reflex_id: str) -> Reflex: ...
    async def resume_reflex(self, reflex_id: str) -> Reflex: ...
    async def delete_reflex(self, reflex_id: str) -> None: ...


class ActionService(Protocol):
    async def list_decisions(self, limit: int) -> list[Decision]: ...
    async def list_actions(self, status: str | None, limit: int) -> list[ActionRecord]: ...
    async def confirm(self, action_id: str) -> ActionRecord: ...
    async def reject(self, action_id: str) -> ActionRecord: ...
    async def undo(self, action_id: str) -> ActionRecord: ...


class HistoryService(Protocol):
    async def list_changes(
        self, device_id: str | None, since: str | None, limit: int
    ) -> list[StateChange]: ...


class CooldownService(Protocol):
    """Device blocking after a user override (D9) and the user's options about it."""

    async def list_cooldowns(self) -> list[DeviceCooldown]:
        """Active cooldowns only."""
        ...

    async def update_cooldown(self, device_id: str, body: CooldownUpdate) -> DeviceCooldown:
        """Shorten / extend / make indefinite (creates one with reason `user` if none is active).
        `minutes=0` unblocks now (returns the ended cooldown). KeyError -> 404 for unknown devices."""
        ...

    async def clear_cooldown(self, device_id: str) -> None:
        """Unblock now. Idempotent."""
        ...


class Services:
    """Container wired in main.py lifespan."""

    def __init__(
        self,
        devices: DeviceService,
        rooms: RoomService,
        sim: SimAdminService,
        plugins: PluginService,
        agent: AgentService,
        actions: ActionService,
        history: HistoryService,
        cooldowns: CooldownService | None = None,
    ) -> None:
        self.devices, self.rooms, self.sim = devices, rooms, sim
        self.plugins, self.agent, self.actions, self.history = plugins, agent, actions, history
        self._cooldowns = cooldowns

    @property
    def cooldowns(self) -> CooldownService:
        if self._cooldowns is None:
            raise NotImplementedError("Cooldown controls are not implemented yet (Team A)")
        return self._cooldowns

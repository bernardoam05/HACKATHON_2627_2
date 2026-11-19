"""Plugin registry: builtin YAML + confirmed plugins from storage.

# Owner: Team A
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.schemas import (
    CATEGORY_DEFAULT_AUTONOMY,
    AutonomyLevel,
    Device,
    DevicePlugin,
    PluginProposal,
    PluginStatus,
)
from app.storage.repository import Repository

from .defaults import load_builtin_plugins

# Capability types that are always `suggest`, whatever a plugin says (CLAUDE.md "Never do").
SECURITY_CAPABILITIES = frozenset(
    {
        "alarmState",
        "securityStatus",
        "sirenSwitch",
        "recording",
        "sdRecording",
        "startCloudRecording",
        "stopCloudRecording",
        "manualCloudRecordingTriggered",
    }
)

# Substrings that make an unknown capability security-sensitive (conservative on purpose).
SECURITY_KEYWORDS = ("alarm", "siren", "record", "lock", "security", "arm")


def is_security_capability(cap_type: str) -> bool:
    """Alarm / siren / camera recording / locks: always `suggest`, whatever a plugin says."""
    if cap_type in SECURITY_CAPABILITIES:
        return True
    low = cap_type.lower()
    return any(k in low for k in SECURITY_KEYWORDS if k != "arm") or low.startswith("arm")


DevicesProvider = Callable[[], Awaitable[list[Device]]]
Proposer = Callable[[Device], Awaitable[PluginProposal]]


class PluginRegistry:
    """Resolves autonomy: confirmed plugin -> builtin -> CATEGORY_DEFAULT_AUTONOMY -> suggest.

    Implements PluginService.
    """

    def __init__(
        self,
        repo: Repository,
        devices: DevicesProvider | None = None,
        proposer: Proposer | None = None,
    ) -> None:
        self._repo = repo
        self._devices = devices
        self._proposer = proposer
        self._builtin = {p.type: p for p in load_builtin_plugins()}

    # --- lookup -----------------------------------------------------------------------------

    async def get(self, type: str) -> DevicePlugin | None:
        """Confirmed plugin first, then builtin. Proposed (unconfirmed) plugins are ignored."""
        stored = await self._repo.get_plugin(type)
        if stored and stored.status == PluginStatus.confirmed:
            return stored
        return self._builtin.get(type)

    async def resolve_autonomy(self, device: Device, cap_type: str) -> AutonomyLevel:
        if is_security_capability(cap_type):
            return AutonomyLevel.suggest
        plugin = await self.get(device.type)
        if plugin is not None:
            return plugin.autonomy_for(cap_type)
        for category in device.categories:
            if category in CATEGORY_DEFAULT_AUTONOMY:
                return CATEGORY_DEFAULT_AUTONOMY[category]
        return AutonomyLevel.suggest

    # --- PluginService ----------------------------------------------------------------------

    async def list_plugins(self) -> list[DevicePlugin]:
        merged = dict(self._builtin)
        for p in await self._repo.list_plugins():
            if p.status == PluginStatus.confirmed or p.type not in merged:
                merged[p.type] = p
        return sorted(merged.values(), key=lambda p: p.type)

    async def unconfigured_types(self) -> list[str]:
        if self._devices is None:
            return []
        types = {d.type for d in await self._devices()}
        return sorted([t for t in types if await self.get(t) is None])

    async def propose(self, type: str) -> PluginProposal:
        if self._proposer is None or self._devices is None:
            raise NotImplementedError("Plugin onboarding (agent/onboarding.py) is not wired yet")
        device = next((d for d in await self._devices() if d.type == type), None)
        if device is None:
            raise KeyError(type)
        proposal = await self._proposer(device)
        proposal.plugin.status = PluginStatus.proposed
        await self._repo.save_plugin(proposal.plugin)
        return proposal

    async def confirm_plugin(self, type: str, plugin: DevicePlugin) -> DevicePlugin:
        if plugin.type != type:
            raise ValueError(f"plugin.type '{plugin.type}' does not match path type '{type}'")
        confirmed = plugin.model_copy(update={"status": PluginStatus.confirmed})
        for spec in confirmed.capabilities:
            if is_security_capability(spec.type):
                spec.autonomy = AutonomyLevel.suggest
        await self._repo.save_plugin(confirmed)
        return confirmed

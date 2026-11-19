"""Read-only agent tools.

# Owner: Team A
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext

from app.devices.registry import DeviceRegistry
from app.plugins.registry import PluginRegistry
from app.storage.repository import Repository

from .prompts import device_summary


@dataclass
class AgentDeps:
    registry: DeviceRegistry
    repo: Repository
    plugins: PluginRegistry
    now: Callable[[], datetime]


def register_tools(agent: Agent[AgentDeps, Any]) -> None:
    """get_home_state, get_device, get_plugin, get_history. No write tools (D10).

    TODO(Team A, phase 2/3): get_presence_facts, get_routine_stats.
    """

    @agent.tool
    async def get_home_state(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
        """Current state of every enabled device."""
        devices = await ctx.deps.registry.list_devices()
        return [device_summary(d) for d in devices if d.enabled]

    @agent.tool
    async def get_device(ctx: RunContext[AgentDeps], device_id: str) -> dict[str, Any]:
        """Current state of one device."""
        try:
            return device_summary(await ctx.deps.registry.get_device(device_id))
        except KeyError as e:
            raise ModelRetry(f"Unknown device id {device_id!r}") from e

    @agent.tool
    async def get_plugin(ctx: RunContext[AgentDeps], device_type: str) -> dict[str, Any] | None:
        """Capability descriptions, writability and autonomy for a device type (null if unknown)."""
        plugin = await ctx.deps.plugins.get(device_type)
        return plugin.model_dump(mode="json") if plugin else None

    @agent.tool
    async def get_history(
        ctx: RunContext[AgentDeps],
        device_id: str,
        capability: str | None = None,
        hours: float = 24,
    ) -> list[dict[str, Any]]:
        """Stored state changes for a device over the last `hours` (oldest first, max 200)."""
        since = datetime.now(UTC) - timedelta(hours=hours)  # history is stored in real UTC time
        changes = await ctx.deps.repo.changes_between(since, device_id=device_id, capability=capability)
        return [
            {"capability": c.capability, "old": c.old, "new": c.new, "at": c.at.isoformat(),
             "causedByAgent": c.causedByAgent}
            for c in changes[-200:]
        ]

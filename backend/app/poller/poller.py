"""Poller: reads all enabled devices, diffs, persists, runs reflexes, wakes the agent.

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from app.devices.registry import DeviceRegistry
from app.schemas import (
    DeviceAddedMsg,
    DeviceRemovedMsg,
    DeviceSource,
    PluginNeededMsg,
    StateChange,
    StateChangedMsg,
    StateSnapshot,
)
from app.storage.repository import Repository

from .antiloop import ANTILOOP_WINDOW, tag_agent_changes
from .diff import diff_snapshots, new_id
from .scheduler import LatestWinsScheduler

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]


class ChangeHook(Protocol):
    async def on_changes(self, changes: list[StateChange]) -> list[StateChange]:
        """React to a stored batch (override detection, reflexes, door timer); return synthetic
        changes to append, e.g. `reflex_executed` (D16)."""
        ...


class PluginLookup(Protocol):
    async def get(self, type: str) -> Any: ...


class Poller:
    """Per-source polling. Each source is diffed against its own previous read, so a slow or
    failing source never produces fake add/remove changes for the other one."""

    def __init__(
        self,
        registry: DeviceRegistry,
        repo: Repository,
        scheduler: LatestWinsScheduler,
        broadcast: Broadcast,
        intervals: dict[DeviceSource, float],
        hooks: list[ChangeHook] | None = None,
        plugins: PluginLookup | None = None,
        background: list[Callable[[], Awaitable[None]]] | None = None,
    ) -> None:
        """`background`: long-running coroutines started with the poll loops (e.g. the reflex
        clock), so they live and die with the poller task."""
        self._background = list(background or [])
        self._registry = registry
        self._repo = repo
        self._scheduler = scheduler
        self._broadcast = broadcast
        self._intervals = intervals
        self._hooks = list(hooks or [])
        self._plugins = plugins
        self._prev: dict[DeviceSource, StateSnapshot] = {}
        self._lock = asyncio.Lock()  # one batch processed at a time across sources

    def current_snapshot(self) -> StateSnapshot:
        devices = [d for s in self._registry.sources for d in self._prev.get(s, _EMPTY).devices]
        return StateSnapshot(id=new_id(), takenAt=datetime.now(UTC), devices=devices)

    async def poll_once(self, source: DeviceSource) -> list[StateChange]:
        """One poll of one source. Returns the stored changes (may be empty)."""
        try:
            devices = await self._registry.read_source(source)
        except Exception as e:
            log.warning("poll %s failed: %r", source.value, e)
            return []
        async with self._lock:
            curr = StateSnapshot(id=new_id(), takenAt=datetime.now(UTC), devices=devices)
            prev = self._prev.get(source)
            self._prev[source] = curr
            result = diff_snapshots(prev, curr)
            if prev is None:
                await self._repo.save_snapshot(self.current_snapshot())
                return []

            enabled = {d.id for d in devices if d.enabled} | {d.id for d in result.removed}
            changes = [c for c in result.changes if c.deviceId in enabled]
            if not changes:
                return []

            recent = await self._repo.actions_since(curr.takenAt - ANTILOOP_WINDOW * 2)
            tag_agent_changes(changes, recent)
            await self._repo.add_changes(changes)
            await self._repo.save_snapshot(self.current_snapshot())

            for hook in self._hooks:  # in order; a failing hook never stops the poller
                try:
                    extra = await hook.on_changes(changes)
                except Exception:
                    log.exception("poller hook %s failed", type(hook).__name__)
                    extra = []
                if extra:
                    await self._repo.add_changes(extra)
                    changes = [*changes, *extra]

        await self._broadcast(StateChangedMsg(changes=changes))
        for dev in result.added:
            await self._broadcast(DeviceAddedMsg(device=dev))
            if self._plugins is not None and await self._plugins.get(dev.type) is None:
                await self._broadcast(PluginNeededMsg(deviceType=dev.type))
        for dev in result.removed:
            await self._broadcast(DeviceRemovedMsg(device=dev))

        if any(not c.causedByAgent for c in changes):
            await self._scheduler.request_run()
        return changes

    async def _loop(self, source: DeviceSource) -> None:
        interval = self._intervals.get(source, 5.0)
        while True:
            await asyncio.sleep(interval)
            await self.poll_once(source)

    async def run(self, on_ready: Callable[[], Awaitable[None]] | None = None) -> None:
        """Baseline poll of every source, then `on_ready()` (startup run), then loop until cancelled."""
        await asyncio.gather(*(self.poll_once(s) for s in self._registry.sources))
        if on_ready is not None:
            try:
                await on_ready()
            except Exception:
                log.exception("poller on_ready failed")
        await asyncio.gather(
            *(self._loop(s) for s in self._registry.sources),
            *(task() for task in self._background),
        )


_EMPTY = StateSnapshot(id="", takenAt=datetime.min.replace(tzinfo=UTC), devices=[])


async def run_poller(services: Any) -> None:
    """Loop: sim every SIM_POLL_SECONDS, Altice every ALTICE_POLL_SECONDS. One source failing never stops the other. Run order per PLAN section 3.

    Expects the Team A wiring to have attached a `Poller` as `services.poller`.
    """
    poller: Poller | None = getattr(services, "poller", None)
    if poller is None:
        raise NotImplementedError("Poller is not wired into Services yet (Team A)")
    await poller.run(getattr(services, "on_ready", None))

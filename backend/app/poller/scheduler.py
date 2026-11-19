"""Latest-wins scheduler (D5).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

log = logging.getLogger(__name__)

RunFn = Callable[[], Awaitable[None]]
StatusFn = Callable[[bool, bool], Awaitable[None]]


class LatestWinsScheduler:
    """request_run(); one run at a time; dirty flag; rerun once on fresh snapshot; broadcast agent_status; optional min gap.

    `run_fn` must read the current state itself (fresh snapshot + all changes since its last run),
    so a rerun after a dirty flag naturally sees everything that happened meanwhile.
    """

    def __init__(
        self,
        run_fn: RunFn,
        on_status: StatusFn | None = None,
        min_gap_seconds: float = 1.0,
    ) -> None:
        self._run_fn = run_fn
        self._on_status = on_status
        self._min_gap = min_gap_seconds
        self.busy = False
        self.dirty = False
        self.last_run_at: datetime | None = None
        self.runs = 0
        self._task: asyncio.Task[None] | None = None

    async def request_run(self) -> None:
        if self.busy:
            if not self.dirty:
                self.dirty = True
                await self._status()
            return
        self.busy = True
        await self._status()
        self._task = asyncio.create_task(self._loop())

    async def wait_idle(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        try:
            while True:
                self.dirty = False
                try:
                    await self._run_fn()
                except Exception:
                    log.exception("agent run failed")
                self.runs += 1
                self.last_run_at = datetime.now(UTC)
                if not self.dirty:
                    break
                if self._min_gap > 0:
                    await asyncio.sleep(self._min_gap)
        finally:
            self.busy = False
            self.dirty = False
            await self._status()

    async def _status(self) -> None:
        if self._on_status is None:
            return
        try:
            await self._on_status(self.busy, self.dirty)
        except Exception:
            log.exception("agent_status broadcast failed")

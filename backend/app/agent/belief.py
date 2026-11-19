"""Belief state and delta/full mode choice (D3).

# Owner: Team A
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from app.storage.repository import Repository, to_utc

MAX_BELIEF_CHARS = 800

Mode = Literal["delta", "full"]


class BeliefStore:
    """Store beliefState; choose mode via FULL_SNAPSHOT_EVERY_RUNS / FULL_SNAPSHOT_EVERY_SECONDS."""

    def __init__(self, repo: Repository, every_runs: int, every_seconds: float) -> None:
        self._repo = repo
        self._every_runs = every_runs
        self._every = timedelta(seconds=every_seconds)
        self._force_full = True  # first run after (re)start always sees the full snapshot

    async def current(self) -> str | None:
        latest = await self._repo.latest_belief()
        return latest[0] if latest else None

    async def choose_mode(self, now: datetime) -> Mode:
        if self._force_full:
            return "full"
        last_full = await self._repo.last_full_run_at()
        if last_full is None or to_utc(now) - last_full >= self._every:
            return "full"
        if await self._repo.runs_since_full() >= self._every_runs:
            return "full"
        return "delta"

    async def save(self, text: str, mode: Mode, now: datetime) -> str:
        text = (text or "").strip()[:MAX_BELIEF_CHARS]
        await self._repo.save_belief(text, mode, now)
        if mode == "full":
            self._force_full = False
        return text

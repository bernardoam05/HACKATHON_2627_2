"""Insight lifecycle (D15).

# Owner: Team A
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from app.schemas import (
    ActionRecord,
    Insight,
    InsightStatus,
    InsightUpdate,
    InsightUpdatedMsg,
    ReflexStatus,
    ReflexUpdatedMsg,
)
from app.storage.repository import Repository

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]

DEFAULT_CREATE_CONFIDENCE = 0.4
DEFAULT_REINFORCE = 0.15
DEFAULT_WEAKEN = 0.2
FEEDBACK_REINFORCE = 0.1
FEEDBACK_WEAKEN = 0.2
MAX_EVIDENCE = 20
REFLEX_PREFIX = "reflex:"  # ActionRecord.decisionId of actions executed by a reflex


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class InsightEngine:
    """Apply InsightUpdates, clamp confidence, drop below INSIGHT_MIN_CONFIDENCE, turn undo/reject into weaken, auto-pause reflex after REFLEX_MAX_UNDOS."""

    def __init__(
        self,
        repo: Repository,
        broadcast: Broadcast,
        min_confidence: float,
        max_reflex_undos: int,
    ) -> None:
        self._repo = repo
        self._broadcast = broadcast
        self._min = min_confidence
        self._max_undos = max_reflex_undos

    async def apply(self, updates: list[InsightUpdate], decision_id: str | None = None) -> list[Insight]:
        """Apply the agent's updates. Unknown ids are ignored. Returns the touched insights."""
        now = datetime.now(UTC)
        touched: list[Insight] = []
        for u in updates:
            insight = await self._apply_one(u, decision_id, now)
            if insight is not None:
                touched.append(insight)
        return touched

    async def _apply_one(
        self, u: InsightUpdate, decision_id: str | None, now: datetime
    ) -> Insight | None:
        evidence = list(u.evidence or [])
        if decision_id:
            evidence.append(decision_id)
        if u.op == "create":
            if not u.statement:
                return None
            insight = Insight(
                id=uuid.uuid4().hex[:12],
                statement=u.statement.strip(),
                confidence=_clamp(u.delta if u.delta is not None else DEFAULT_CREATE_CONFIDENCE),
                evidence=evidence[-MAX_EVIDENCE:],
                createdAt=now,
                lastConfirmedAt=now,
            )
            return await self._save(insight)

        if not u.insightId:
            return None
        insight = await self._repo.get_insight(u.insightId)
        if insight is None or insight.status != InsightStatus.active:
            return None
        if u.op == "delete":
            return await self._save(insight.model_copy(update={"status": InsightStatus.dropped}))
        if u.op == "reinforce":
            delta = abs(u.delta) if u.delta is not None else DEFAULT_REINFORCE
            return await self._adjust(insight, delta, evidence, now)
        if u.op == "weaken":
            delta = abs(u.delta) if u.delta is not None else DEFAULT_WEAKEN
            return await self._adjust(insight, -delta, evidence, now)
        return None

    async def _adjust(
        self, insight: Insight, delta: float, evidence: list[str], now: datetime
    ) -> Insight:
        update: dict = {
            "confidence": _clamp(insight.confidence + delta),
            "evidence": [*insight.evidence, *evidence][-MAX_EVIDENCE:],
        }
        if delta > 0:
            update["lastConfirmedAt"] = now
        return await self._save(insight.model_copy(update=update))

    async def _save(self, insight: Insight) -> Insight:
        if insight.status == InsightStatus.active and insight.confidence < self._min:
            insight = insight.model_copy(update={"status": InsightStatus.dropped})
        await self._repo.save_insight(insight)
        await self._broadcast(InsightUpdatedMsg(insight=insight))
        return insight

    async def get(self, insight_id: str) -> Insight | None:
        return await self._repo.get_insight(insight_id)

    # --- feedback ---------------------------------------------------------------------------

    async def linked_insights(self, action: ActionRecord) -> list[str]:
        """Reflex action -> its source insight; agent action -> insights its decision touched."""
        if action.decisionId.startswith(REFLEX_PREFIX):
            reflex = await self._repo.get_reflex(action.decisionId.removeprefix(REFLEX_PREFIX))
            return [reflex.sourceInsightId] if reflex and reflex.sourceInsightId else []
        decision = await self._repo.get_decision(action.decisionId)
        if decision is None:
            return []
        return [u.insightId for u in decision.insightUpdates if u.insightId and u.op != "delete"]

    async def on_negative(self, action: ActionRecord, kind: str) -> None:
        """Undo / reject / manual override: weaken linked insights; count reflex undos."""
        note = f"{kind}:{action.id}"
        for insight_id in await self.linked_insights(action):
            await self.apply([InsightUpdate(op="weaken", insightId=insight_id,
                                            delta=FEEDBACK_WEAKEN, evidence=[note])])
        if action.decisionId.startswith(REFLEX_PREFIX):
            reflex = await self._repo.get_reflex(action.decisionId.removeprefix(REFLEX_PREFIX))
            if reflex is None:
                return
            undos = reflex.undos + 1
            status = reflex.status
            if undos >= self._max_undos and status == ReflexStatus.active:
                status = ReflexStatus.paused
                log.info("reflex %s auto-paused after %d undos", reflex.id, undos)
            reflex = reflex.model_copy(update={"undos": undos, "status": status})
            await self._repo.save_reflex(reflex)
            await self._broadcast(ReflexUpdatedMsg(reflex=reflex))

    async def on_positive(self, action: ActionRecord) -> None:
        for insight_id in await self.linked_insights(action):
            await self.apply([InsightUpdate(op="reinforce", insightId=insight_id,
                                            delta=FEEDBACK_REINFORCE,
                                            evidence=[f"confirm:{action.id}"])])

"""Reflex engine (D16).

# Owner: Team A
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.devices.registry import DeviceRegistry
from app.plugins.registry import PluginRegistry
from app.schemas import (
    AutonomyLevel,
    ProposedAction,
    Reflex,
    ReflexExecutedMsg,
    ReflexProposal,
    ReflexProposedMsg,
    ReflexStatus,
    ReflexTrigger,
    ReflexUpdate,
    ReflexUpdatedMsg,
    StateChange,
    TimeWindow,
)
from app.storage.repository import Repository

from .executor import Executor
from .insights import REFLEX_PREFIX

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]
REFLEX_EXECUTED = "reflex_executed"  # capability of the synthetic change reported to the agent


class ReflexDraft(BaseModel):
    """What the LLM proposes; id/status/createdAt are set by code."""

    name: str
    triggers: list[ReflexTrigger]
    triggerLogic: Literal["all", "any"] = "all"
    actions: list[ProposedAction]
    sourceInsightId: str
    rationale: str = ""


class ReflexUpdateDraft(BaseModel):
    reflexId: str
    op: Literal["pause", "delete", "modify"]
    triggers: list[ReflexTrigger] | None = None
    triggerLogic: Literal["all", "any"] | None = None
    actions: list[ProposedAction] | None = None
    reason: str = ""


# --- trigger evaluation (pure) -------------------------------------------------------------


def _truthy_eq(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    return str(actual).lower() == str(expected).lower()


def condition(op: str, actual: Any, expected: Any) -> bool:
    if op == "changed":
        return True
    if op == "=":
        return _truthy_eq(actual, expected)
    try:
        a, e = float(actual), float(expected)
    except (TypeError, ValueError):
        return False
    return a < e if op == "<" else a > e if op == ">" else False


def in_window(window: TimeWindow | None, now: datetime) -> bool:
    if window is None:
        return True
    if window.weekdays and now.weekday() not in window.weekdays:  # 0 = Monday
        return False
    hm = now.strftime("%H:%M")
    start, end = window.from_, window.to
    return start <= hm <= end if start <= end else hm >= start or hm <= end


def is_triggered(
    reflex: Reflex, changes: list[StateChange], state: dict[tuple[str, str], Any], now: datetime
) -> bool:
    """`any`: some trigger matched by a change in this batch. `all`: at least one trigger matched
    by a change and every other trigger true in the current state (`changed` needs a change)."""
    by_change: list[bool] = []
    satisfied: list[bool] = []
    for t in reflex.triggers:
        if not in_window(t.timeWindow, now):
            return False
        hit = any(
            c.deviceId == t.deviceId and c.capability == t.capability
            and condition(t.operator, c.new, t.value)
            for c in changes
        )
        key = (t.deviceId, t.capability)
        held = t.operator != "changed" and key in state and condition(t.operator, state[key], t.value)
        by_change.append(hit)
        satisfied.append(hit or held)
    if not reflex.triggers or not any(by_change):
        return False
    return True if reflex.triggerLogic == "any" else all(satisfied)


# --- engine --------------------------------------------------------------------------------


class ReflexEngine:
    """Evaluate active reflexes on each change batch before waking the agent; execute via executor (never suggest); log as agent actions; append reflex_executed change."""

    def __init__(
        self,
        repo: Repository,
        registry: DeviceRegistry,
        executor: Executor,
        plugins: PluginRegistry,
        broadcast: Broadcast,
        now: Callable[[], datetime],
        min_confidence: float,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._executor = executor
        self._plugins = plugins
        self._broadcast = broadcast
        self._now = now
        self._min_conf = min_confidence

    async def on_changes(self, changes: list[StateChange]) -> list[StateChange]:
        """Poller hook. Agent-caused changes never trigger reflexes (no chains, no loops)."""
        user_changes = [c for c in changes if not c.causedByAgent]
        if not user_changes:
            return []
        active = await self._repo.list_reflexes(ReflexStatus.active.value)
        if not active:
            return []
        state = {(d.id, c.type): c.value for d in self._registry.cached() for c in d.capabilities}
        now = self._now()
        out: list[StateChange] = []
        for reflex in active:
            if is_triggered(reflex, user_changes, state, now):
                change = await self._execute(reflex)
                if change is not None:
                    out.append(change)
        return out

    async def _execute(self, reflex: Reflex) -> StateChange | None:
        records = []
        for action in reflex.actions:
            record = await self._executor.apply(
                f"{REFLEX_PREFIX}{reflex.id}", action, never_suggest=True
            )
            if record is not None:
                records.append(record)
        if not records:
            return None
        at = datetime.now(UTC)
        reflex = reflex.model_copy(update={"executions": reflex.executions + 1})
        await self._repo.save_reflex(reflex)
        await self._repo.log_reflex_execution(reflex.id, [r.id for r in records], at)
        await self._broadcast(ReflexExecutedMsg(reflexId=reflex.id, actions=records))
        log.info("reflex %s executed %d actions", reflex.id, len(records))
        return StateChange(
            id=uuid.uuid4().hex,
            deviceId=f"{REFLEX_PREFIX}{reflex.id}",
            capability=REFLEX_EXECUTED,
            new={
                "reflexId": reflex.id,
                "name": reflex.name,
                "actions": [
                    {"actionId": r.id, "deviceId": r.deviceId, "type": r.update.type,
                     "value": r.update.value, "status": r.status.value}
                    for r in records
                ],
            },
            at=at,
            causedByAgent=True,  # reported in the next delta, but never wakes the agent by itself
        )

    # --- proposals and updates from the agent -----------------------------------------------

    async def _safe_actions(self, actions: list[ProposedAction]) -> list[ProposedAction]:
        """Drop actions a reflex may never perform: unknown devices and `suggest` capabilities."""
        safe: list[ProposedAction] = []
        for a in actions:
            try:
                device = await self._registry.get_device(a.deviceId)
            except KeyError:
                continue
            if await self._plugins.resolve_autonomy(device, a.update.type) != AutonomyLevel.suggest:
                safe.append(a)
        return safe

    async def accept_proposals(self, drafts: list[ReflexDraft]) -> list[ReflexProposal]:
        accepted: list[ReflexProposal] = []
        existing = await self._repo.list_reflexes()
        for d in drafts:
            insight = await self._repo.get_insight(d.sourceInsightId)
            if insight is None or insight.confidence < self._min_conf:
                log.info("reflex proposal %r dropped: insight not confident enough", d.name)
                continue
            actions = await self._safe_actions(d.actions)
            if not actions or not d.triggers:
                continue
            if any(_same(r, d.triggers, actions) for r in existing):
                continue
            reflex = Reflex(
                id=uuid.uuid4().hex[:12],
                name=d.name,
                triggers=d.triggers,
                triggerLogic=d.triggerLogic,
                actions=actions,
                sourceInsightId=d.sourceInsightId,
                status=ReflexStatus.proposed,
                createdAt=datetime.now(UTC),
            )
            await self._repo.save_reflex(reflex)
            existing.append(reflex)
            await self._broadcast(ReflexProposedMsg(reflex=reflex))
            accepted.append(ReflexProposal(reflex=reflex, rationale=d.rationale))
        return accepted

    async def apply_agent_updates(self, drafts: list[ReflexUpdateDraft]) -> list[ReflexUpdate]:
        """The agent may pause, delete or modify (-> back to `proposed`), never resume/activate."""
        applied: list[ReflexUpdate] = []
        for d in drafts:
            reflex = await self._repo.get_reflex(d.reflexId)
            if reflex is None or reflex.status == ReflexStatus.deleted:
                continue
            if d.op == "pause":
                if reflex.status != ReflexStatus.active:
                    continue
                reflex = reflex.model_copy(update={"status": ReflexStatus.paused})
            elif d.op == "delete":
                reflex = reflex.model_copy(update={"status": ReflexStatus.deleted})
            else:
                changes: dict[str, Any] = {"status": ReflexStatus.proposed}
                if d.triggers:
                    changes["triggers"] = d.triggers
                if d.triggerLogic:
                    changes["triggerLogic"] = d.triggerLogic
                if d.actions is not None:
                    changes["actions"] = await self._safe_actions(d.actions)
                reflex = reflex.model_copy(update=changes)
                if not reflex.actions:
                    reflex = reflex.model_copy(update={"status": ReflexStatus.deleted})
            await self._repo.save_reflex(reflex)
            await self._broadcast(ReflexUpdatedMsg(reflex=reflex))
            applied.append(ReflexUpdate(reflexId=reflex.id, op=d.op, reflex=reflex, reason=d.reason))
        return applied

    # --- user operations (AgentService) -----------------------------------------------------

    async def move(self, reflex_id: str, allowed: set[ReflexStatus], to: ReflexStatus) -> Reflex:
        reflex = await self._repo.get_reflex(reflex_id)
        if reflex is None or reflex.status == ReflexStatus.deleted:
            raise KeyError(reflex_id)
        if reflex.status not in allowed:
            raise ValueError(f"reflex {reflex_id} is {reflex.status.value}")
        update: dict[str, Any] = {"status": to}
        if to == ReflexStatus.active and reflex.status == ReflexStatus.paused:
            update["undos"] = 0  # resuming gives the reflex a fresh undo budget
        reflex = reflex.model_copy(update=update)
        await self._repo.save_reflex(reflex)
        await self._broadcast(ReflexUpdatedMsg(reflex=reflex))
        return reflex


def _same(r: Reflex, triggers: list[ReflexTrigger], actions: list[ProposedAction]) -> bool:
    if r.status == ReflexStatus.deleted:
        return False

    def key_t(ts: list[ReflexTrigger]) -> set:
        return {(t.deviceId, t.capability, t.operator, str(t.value)) for t in ts}

    def key_a(acts: list[ProposedAction]) -> set:
        return {(a.deviceId, a.update.type, str(a.update.value)) for a in acts}

    return key_t(r.triggers) == key_t(triggers) and key_a(r.actions) == key_a(actions)

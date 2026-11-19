"""Pydantic AI agent: output_type=Decision, read-only tools only (D10).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.config import Settings
from app.devices.registry import DeviceRegistry
from app.devices.rooms import RoomStore
from app.plugins.registry import PluginRegistry
from app.schemas import (
    AgentRunInput,
    ChatRequest,
    ChatResponse,
    Decision,
    DecisionMsg,
    DecisionTrigger,
    DevicePlugin,
    Insight,
    InsightUpdate,
    ProposedAction,
    Reflex,
    ReflexStatus,
    StateSnapshot,
)
from app.storage.repository import Repository

from .belief import BeliefStore
from .executor import Executor
from .feedback import FeedbackService
from .insights import InsightEngine
from .prompts import build_prompt, instructions
from .reflexes import ReflexDraft, ReflexEngine, ReflexUpdateDraft
from .tools import AgentDeps, register_tools

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]


class AgentOutput(BaseModel):
    """What the LLM returns. Ids, timestamps and trigger are filled in by code."""

    explanation: str = Field(description="Short user-facing explanation in PT-PT")
    inferredRoom: str | None = None
    inferredActivity: str | None = None
    actions: list[ProposedAction] = []
    confidence: float = Field(default=0.5, ge=0, le=1)
    beliefState: str = Field(description="Your updated belief state, <= ~800 chars")
    insightUpdates: list[InsightUpdate] = []
    reflexProposals: list[ReflexDraft] = []
    reflexUpdates: list[ReflexUpdateDraft] = []


class Clock:
    """Real UTC clock, or NOW_OVERRIDE advancing in real time from process start (demos)."""

    def __init__(self, override: str | None) -> None:
        self._start = datetime.now(UTC)
        self._base = datetime.fromisoformat(override) if override else None

    def now(self) -> datetime:
        if self._base is None:
            return datetime.now(UTC)
        return self._base + (datetime.now(UTC) - self._start)


class HomeAgent:
    """run(AgentRunInput) -> Decision; chat(); implements AgentService."""

    def __init__(
        self,
        settings: Settings,
        registry: DeviceRegistry,
        repo: Repository,
        plugins: PluginRegistry,
        rooms: RoomStore,
        executor: Executor,
        broadcast: Broadcast,
        insights: InsightEngine,
        reflexes: ReflexEngine,
        feedback: FeedbackService,
        clock: Clock | None = None,
        model: Model | str | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._repo = repo
        self._plugins = plugins
        self._rooms = rooms
        self._executor = executor
        self._broadcast = broadcast
        self._insights = insights
        self._reflexes = reflexes
        self._feedback = feedback
        self._belief = BeliefStore(
            repo, settings.FULL_SNAPSHOT_EVERY_RUNS, settings.FULL_SNAPSHOT_EVERY_SECONDS
        )
        self.clock = clock or Clock(settings.NOW_OVERRIDE)
        self._model_name = str(model or settings.AGENT_MODEL)
        self._agent: Agent[AgentDeps, AgentOutput] = Agent(
            model or settings.AGENT_MODEL,
            deps_type=AgentDeps,
            output_type=AgentOutput,
            instructions=instructions(settings.REFLEX_MIN_CONFIDENCE),
            defer_model_check=True,
            retries=2,
        )
        register_tools(self._agent)
        self._lock = asyncio.Lock()  # one LLM run at a time (state runs and chat)
        self._last_cutoff: datetime | None = None  # real UTC time of the last consumed change
        self._wake_reasons: list[str] = []
        self.scheduler: Any = None  # set by wiring, used for status()

    @property
    def llm(self) -> Agent[AgentDeps, AgentOutput]:
        return self._agent

    # --- runs -------------------------------------------------------------------------------

    async def run_cycle(self) -> Decision | None:
        """Scheduler entry point: reason on all changes since the last run."""
        return await self._run(DecisionTrigger.state_change)

    async def wake(self, reason: str) -> None:
        """Run even without new changes (e.g. the empty-house timer)."""
        self._wake_reasons.append(reason)
        if self.scheduler is not None:
            await self.scheduler.request_run()

    async def chat(self, body: ChatRequest) -> ChatResponse:
        decision = await self._run(DecisionTrigger.chat, user_message=body.message)
        assert decision is not None
        return ChatResponse(reply=decision.explanation, decision=decision)

    async def _run(
        self, trigger: DecisionTrigger, user_message: str | None = None
    ) -> Decision | None:
        async with self._lock:
            cutoff = datetime.now(UTC)
            since = self._last_cutoff or await self._initial_cutoff(cutoff)
            changes = await self._repo.changes_between(since, cutoff)
            wake_reasons, self._wake_reasons = self._wake_reasons, []
            if (
                trigger == DecisionTrigger.state_change
                and not wake_reasons
                and not any(not c.causedByAgent for c in changes)
            ):
                self._last_cutoff = cutoff
                return None

            run_input = await self.build_input(changes, chat=user_message is not None)
            prompt = build_prompt(
                run_input,
                reflexes=await self._repo.list_reflexes(),
                cooldowns=list(self._feedback.cooldowns()),
                wake_reasons=wake_reasons,
            )
            if user_message is not None:
                prompt += (
                    f"\n\nThe user says: {user_message}\n"
                    "Answer them in `explanation` and propose the actions they ask for."
                )

            deps = AgentDeps(
                self._registry, self._repo, self._plugins, self.clock.now,
                empty_after_seconds=self._settings.EMPTY_AFTER_MINUTES * 60,
            )
            try:
                result = await self._agent.run(prompt, deps=deps)
            except BaseException:
                self._wake_reasons = wake_reasons + self._wake_reasons  # retry them next run
                raise
            decision = await self._finish(result.output, trigger, run_input)
            self._last_cutoff = cutoff
            return decision

    async def _initial_cutoff(self, cutoff: datetime) -> datetime:
        latest = await self._repo.latest_belief()
        if latest is not None:
            return latest[2]
        return cutoff - timedelta(minutes=5)

    async def build_input(self, changes: list, chat: bool = False) -> AgentRunInput:
        now = self.clock.now()
        mode = "full" if chat else await self._belief.choose_mode(now)
        devices = await self._registry.list_devices()
        snapshot = (
            StateSnapshot(id=uuid.uuid4().hex, takenAt=datetime.now(UTC), devices=devices)
            if mode == "full"
            else None
        )
        if mode == "full":
            types = {d.type for d in devices if d.enabled}
        else:
            changed = {c.deviceId for c in changes}
            types = {d.type for d in devices if d.id in changed}
        plugins: list[DevicePlugin] = [
            p for t in sorted(types) if (p := await self._plugins.get(t)) is not None
        ]
        recent = await self._repo.actions_since(datetime.now(UTC) - timedelta(minutes=30))
        return AgentRunInput(
            mode=mode,
            snapshot=snapshot,
            changesSinceLastRun=changes,
            beliefState=await self._belief.current(),
            insights=await self._repo.list_insights(self._settings.INSIGHTS_IN_PROMPT),
            recentActions=recent[-20:],
            feedback=await self._repo.recent_feedback(10),
            rooms=await self._rooms.list_rooms(),
            plugins=plugins,
            now=now,
        )

    async def _finish(
        self, out: AgentOutput, trigger: DecisionTrigger, run_input: AgentRunInput
    ) -> Decision:
        belief = await self._belief.save(out.beliefState, run_input.mode, datetime.now(UTC))
        decision_id = uuid.uuid4().hex
        await self._insights.apply(out.insightUpdates, decision_id)
        proposals = await self._reflexes.accept_proposals(out.reflexProposals)
        reflex_updates = await self._reflexes.apply_agent_updates(out.reflexUpdates)
        decision = Decision(
            id=decision_id,
            createdAt=datetime.now(UTC),
            trigger=trigger,
            explanation=out.explanation,
            inferredRoom=out.inferredRoom,
            inferredActivity=out.inferredActivity,
            actions=out.actions,
            confidence=out.confidence,
            beliefState=belief,
            insightUpdates=out.insightUpdates,
            reflexProposals=proposals,
            reflexUpdates=reflex_updates,
        )
        await self._repo.save_decision(decision)
        await self._executor.execute(decision, respect_cooldown=trigger != DecisionTrigger.chat)
        if decision.actions or proposals or trigger == DecisionTrigger.chat:
            await self._broadcast(DecisionMsg(decision=decision))
        return decision

    # --- AgentService -----------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        s = self.scheduler
        last = s.last_run_at if s is not None else None
        return {
            "busy": bool(s.busy) if s is not None else self._lock.locked(),
            "dirty": bool(s.dirty) if s is not None else False,
            "lastRunAt": last.isoformat() if last else None,
            "model": self._model_name,
            "beliefState": await self._belief.current() or "",
            "runsSinceFullSnapshot": await self._repo.runs_since_full(),
        }

    async def list_insights(self) -> list[Insight]:
        return await self._repo.list_insights()

    async def delete_insight(self, insight_id: str) -> None:
        if not await self._repo.delete_insight(insight_id):
            raise KeyError(insight_id)

    async def list_reflexes(self, status: str | None) -> list[Reflex]:
        return await self._repo.list_reflexes(status)

    async def confirm_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.proposed}, ReflexStatus.active)

    async def pause_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.active}, ReflexStatus.paused)

    async def resume_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.paused}, ReflexStatus.active)

    async def delete_reflex(self, reflex_id: str) -> None:
        await self._reflexes.move(reflex_id, set(ReflexStatus), ReflexStatus.deleted)

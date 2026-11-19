"""Pydantic AI agent: output_type=Decision, read-only tools only (D10).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelUnavailableError,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.usage import UsageLimits

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
from .prompts import build_prompt, instructions, room_of
from .reflexes import ReflexDraft, ReflexEngine, ReflexUpdateDraft
from .tools import (
    DOOR_CAPABILITIES,
    AgentDeps,
    compute_presence_facts,
    compute_routine_stats,
    register_tools,
)

log = logging.getLogger(__name__)

Broadcast = Callable[[BaseModel], Awaitable[None]]

# Waits between attempts when the model provider fails transiently (503 overloaded, 429, network).
LLM_RETRY_DELAYS: tuple[float, ...] = (2, 5, 15)
# Hard cap on model round-trips per run (tool calls + final answer). Without it a weak model can
# loop on tools for minutes (seen: 19 calls / 352 s at startup) while the house waits.
MAX_MODEL_REQUESTS = 4  # legit runs need 1-2 (e.g. routine stats + answer); worst case 4 + 1
NO_TOOLS_NOTE = (
    "\n\nThis is a quick reaction run: no tools are available. Everything you need is above "
    "(changes, devices in the affected rooms, expected behaviours). Decide now."
)
FORCE_ANSWER = (
    "\n\nYou already used all your tool calls for this run. No tools are available now: "
    "decide from the information above and return your decision immediately."
)
MAX_RETRY_AFTER = 30.0  # honour the provider's Retry-After, but never block runs longer than this
TRANSIENT_HTTP = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def is_transient_llm_error(exc: BaseException) -> bool:
    """Worth retrying: overloaded/rate-limited provider or network trouble.
    Not retried: bad key (401/403), unknown model, invalid request, our own bugs, and an
    exhausted quota (429 "check your plan and billing", no Retry-After): waiting won't help.
    A FallbackModel raises a group with one error per model: transient if any of them is."""
    if isinstance(exc, BaseExceptionGroup):
        return any(is_transient_llm_error(e) for e in exc.exceptions)
    if isinstance(exc, ModelQuotaExceededError):
        return getattr(exc, "retry_after", None) is not None  # per-minute limit: yes; plan: no
    if isinstance(exc, ModelUnavailableError):
        return True
    if isinstance(exc, ModelHTTPError):
        return exc.status_code in TRANSIENT_HTTP
    if isinstance(exc, TimeoutError | ConnectionError | httpx.TransportError):
        return True
    if isinstance(exc, ModelAPIError):  # API error without an HTTP status: connection level
        return True
    return False


def _retry_after(exc: BaseException) -> float | None:
    if isinstance(exc, BaseExceptionGroup):
        waits = [w for e in exc.exceptions if (w := _retry_after(e)) is not None]
        return min(waits) if waits else None
    value = getattr(exc, "retry_after", None)
    try:
        return min(float(value), MAX_RETRY_AFTER) if value is not None else None
    except (TypeError, ValueError):
        return None


# AGENT_MODEL=auto: Google models in order of daily headroom on the free tier (Flash Lite:
# 15 RPM / 500 RPD each), then the Flash models (5 RPM / 20 RPD each) as extra reserve.
AUTO_MODELS: tuple[str, ...] = (
    "google:gemini-3.5-flash-lite",
    "google:gemini-3.1-flash-lite",
    "google:gemini-3.8-flash",
    "google:gemini-3.6-flash",
    "google:gemini-3-flash",
    "google:gemini-2.5-flash",
)


def _log_run(trigger: DecisionTrigger, seconds: float, result: Any) -> None:
    """One line per agent run: duration, model round-trips, models used, tools called."""
    try:
        responses = [m for m in result.all_messages() if isinstance(m, ModelResponse)]
    except Exception:  # noqa: BLE001 - logging must never break a run
        return
    tools = [p.tool_name for m in responses for p in m.parts if isinstance(p, ToolCallPart)]
    models = sorted({m.model_name for m in responses if m.model_name})
    log.info("agent run done (%s) in %.1fs: %d model requests via %s, tools=%s",
             trigger.value, seconds, len(responses), ",".join(models) or "?", tools)


def model_names(spec: str) -> list[str]:
    """AGENT_MODEL: one model, a comma-separated fallback list, or `auto`."""
    if spec.strip().lower() == "auto":
        return list(AUTO_MODELS)
    return [m.strip() for m in spec.split(",") if m.strip()]


def build_model(spec: str) -> Model | str:
    """One name -> that model. Several -> FallbackModel: on any API error (503 overloaded,
    429 quota, network) the next model is tried at once; each Gemini model has its own quota."""
    names = model_names(spec)
    if len(names) <= 1:
        return names[0] if names else spec
    return FallbackModel(*names)


class WakeUp(BaseModel):
    at: str = Field(description='House local time "HH:MM" (next occurrence) or ISO datetime')
    reason: str


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
    wakeUps: list[WakeUp] = []


class Clock:
    """Real UTC clock, or NOW_OVERRIDE advancing in real time from process start (demos)."""

    def __init__(self, override: str | None) -> None:
        self._start = datetime.now(UTC)
        self._base = datetime.fromisoformat(override) if override else None

    def now(self) -> datetime:
        if self._base is None:
            return datetime.now(UTC)
        return self._base + (datetime.now(UTC) - self._start)


MAX_WAKE_AHEAD = timedelta(hours=24)


class WakeScheduler:
    """Timers the agent asks for (`AgentOutput.wakeUps`): nothing changes in the house at 07:25,
    so without them a time-based routine (S3) could never be acted on."""

    def __init__(self, clock: Clock, wake: Callable[[str], Awaitable[None]]) -> None:
        self._clock = clock
        self._wake = wake
        self._timers: dict[str, tuple[datetime, str, asyncio.Task[None]]] = {}

    def resolve(self, at: str) -> datetime | None:
        """"HH:MM" -> next occurrence; ISO -> as is. Same tz-awareness as the clock."""
        now = self._clock.now()
        try:
            if len(at) <= 5 and ":" in at:
                h, m = (int(x) for x in at.split(":"))
                when = now.replace(hour=h, minute=m, second=0, microsecond=0)
                return when if when > now else when + timedelta(days=1)
            when = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None and now.tzinfo is not None:
            when = when.replace(tzinfo=now.tzinfo)
        elif when.tzinfo is not None and now.tzinfo is None:
            when = when.astimezone(UTC).replace(tzinfo=None)
        return when

    def schedule(self, at: str, reason: str) -> datetime | None:
        when = self.resolve(at)
        now = self._clock.now()
        if when is None or when < now - timedelta(seconds=1) or when - now > MAX_WAKE_AHEAD:
            log.info("wake-up %r ignored (unparseable or out of range)", at)
            return None
        key = when.strftime("%Y-%m-%dT%H:%M")
        if key in self._timers and not self._timers[key][2].done():
            return when  # one timer per minute: the agent re-requesting is a no-op
        delay = max(0.0, (when - now).total_seconds())
        task = asyncio.create_task(self._fire(delay, key, reason))
        self._timers[key] = (when, reason, task)
        return when

    async def _fire(self, delay: float, key: str, reason: str) -> None:
        await asyncio.sleep(delay)
        self._timers.pop(key, None)
        await self._wake(f"Scheduled wake-up you asked for: {reason}")

    def pending(self) -> list[tuple[datetime, str]]:
        return sorted((w, r) for w, r, t in self._timers.values() if not t.done())

    def cancel_all(self) -> None:
        for _, _, t in self._timers.values():
            t.cancel()
        self._timers.clear()


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
        self._model_name = str(model) if model else ",".join(model_names(settings.AGENT_MODEL))
        self._agent: Agent[AgentDeps, AgentOutput] = Agent(
            model or build_model(settings.AGENT_MODEL),
            deps_type=AgentDeps,
            output_type=AgentOutput,
            instructions=instructions(settings.REFLEX_MIN_CONFIDENCE),
            defer_model_check=True,
            retries=2,
        )
        register_tools(self._agent)
        # Same model and instructions, no tools: used when a run hits MAX_MODEL_REQUESTS, so the
        # agent still decides instead of the run failing (one extra request).
        self._final_agent: Agent[None, AgentOutput] = Agent(
            model or build_model(settings.AGENT_MODEL),
            output_type=AgentOutput,
            instructions=instructions(settings.REFLEX_MIN_CONFIDENCE),
            defer_model_check=True,
            retries=1,
        )
        self._lock = asyncio.Lock()  # one LLM run at a time (state runs and chat)
        self._last_cutoff: datetime | None = None  # real UTC time of the last consumed change
        self._wake_reasons: list[str] = []
        self.retry_delays: tuple[float, ...] = LLM_RETRY_DELAYS
        self._startup = False
        self.wakeups = WakeScheduler(self.clock, self.wake)
        self.scheduler: Any = None  # set by wiring, used for status()

    @property
    def llm(self) -> Agent[AgentDeps, AgentOutput]:
        return self._agent

    # --- runs -------------------------------------------------------------------------------

    async def run_cycle(self) -> Decision | None:
        """Scheduler entry point: reason on all changes since the last run."""
        if self._startup:
            self._startup = False
            return await self._run(DecisionTrigger.startup)
        return await self._run(DecisionTrigger.state_change)

    async def request_startup(self) -> None:
        """First run after the initial poll: full snapshot, check routines, schedule wake-ups."""
        self._startup = True
        await self.wake(
            "The backend just started. Look at the plugs' routine stats below, schedule wakeUps "
            "for the consistent routines you find, and answer."
        )

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
            devices = await self._registry.list_devices()
            presence, routines = await self._inline_facts(trigger, changes, wake_reasons, devices)
            prompt = build_prompt(
                run_input,
                reflexes=await self._repo.list_reflexes(),
                cooldowns=list(self._feedback.cooldowns()),
                wake_reasons=wake_reasons,
                wakeups=[f"{w.isoformat(timespec='minutes')} {r}" for w, r in self.wakeups.pending()],
                devices=devices,
                presence=presence,
                routines=routines,
                all_devices=bool(wake_reasons),
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
            # Tools only where they add something: chat, startup, full mode, or a wake-up
            # (door timer -> presence facts, routine -> stats). Plain deltas: 1 request.
            # Automatic runs get the facts they would ask for inlined (presence after a door
            # event or wake-up, routine stats at startup / routine wake-ups) and decide in one
            # request. Only chat keeps the tools, for questions the prompt cannot anticipate.
            use_tools = trigger == DecisionTrigger.chat
            if not use_tools:
                prompt += NO_TOOLS_NOTE
            started = time.monotonic()
            log.info("agent run started (%s, %s mode, %d changes, tools=%s)",
                     trigger.value, run_input.mode, len(changes), use_tools)
            try:
                result = await self._run_llm(prompt, deps, use_tools=use_tools)
            except BaseException as e:
                log.info("agent run failed after %.1fs: %s", time.monotonic() - started,
                         type(e).__name__)
                self._wake_reasons = wake_reasons + self._wake_reasons  # retry them next run
                raise
            _log_run(trigger, time.monotonic() - started, result)
            decision = await self._finish(result.output, trigger, run_input)
            self._last_cutoff = cutoff
            return decision

    async def _inline_facts(
        self, trigger: DecisionTrigger, changes: list, wake_reasons: list[str], devices: list
    ) -> tuple[dict | None, list[dict] | None]:
        """Facts the agent would otherwise fetch with tools (same raw data, no inference)."""
        presence = None
        door_changed = any(c.capability in DOOR_CAPABILITIES for c in changes)
        if door_changed or wake_reasons or trigger == DecisionTrigger.startup:
            facts = await compute_presence_facts(
                self._registry, self._repo, self._settings.EMPTY_AFTER_MINUTES * 60
            )
            presence = facts.model_dump(mode="json")
        routines = None
        routine_wake = any("Scheduled wake-up" in w for w in wake_reasons)
        if trigger == DecisionTrigger.startup or routine_wake:
            routines = []
            for d in devices:
                if d.enabled and d.type == "plug":
                    stats = await compute_routine_stats(self._repo, d.id, "instantPower")
                    stats.pop("byDay", None)  # the summaries are enough; keeps the prompt small
                    routines.append({"name": d.alias or d.name, **stats})
        return presence, routines

    async def _run_llm(self, prompt: str, deps: AgentDeps, use_tools: bool = True) -> Any:
        """agent.run with retries on transient provider errors (503 overloaded, 429, network).
        Runs stay serialised meanwhile; changes arriving during the wait set the scheduler's
        dirty flag and are picked up by the next run (D5).

        `use_tools=False` (plain delta runs): the prompt already carries the changes, the
        devices of the affected rooms and their plugins, so one request decides."""
        attempts = len(self.retry_delays) + 1
        for attempt in range(1, attempts + 1):
            try:
                if not use_tools:
                    return await self._final_agent.run(
                        prompt, usage_limits=UsageLimits(request_limit=2)
                    )
                try:
                    return await self._agent.run(
                        prompt, deps=deps,
                        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
                    )
                except UsageLimitExceeded:
                    log.warning("agent used %d model requests without deciding; asking for the "
                                "decision now, without tools", MAX_MODEL_REQUESTS)
                    return await self._final_agent.run(
                        prompt + FORCE_ANSWER, usage_limits=UsageLimits(request_limit=2)
                    )
            except Exception as e:
                if attempt == attempts or not is_transient_llm_error(e):
                    raise
                delay = _retry_after(e) or self.retry_delays[attempt - 1]
                log.warning("model %s unavailable (%s), retry %d/%d in %.0fs",
                            self._model_name, type(e).__name__, attempt, attempts - 1, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

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
            # Changed devices plus the devices in their rooms (the agent may act on those, e.g.
            # motion in a room -> its light), so their plugins' expectedBehaviours are visible.
            changed = {c.deviceId for c in changes}
            rooms = {room_of(d) for d in devices if d.id in changed and room_of(d)}
            types = {d.type for d in devices
                     if d.enabled and (d.id in changed or room_of(d) in rooms)}
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
        for w in out.wakeUps:
            self.wakeups.schedule(w.at, w.reason)
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

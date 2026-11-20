"""Pydantic AI agent: output_type=Decision, read-only tools only (D10).

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
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
    FallbackInfo,
    Insight,
    InsightUpdate,
    ProposedAction,
    Reflex,
    ReflexCreate,
    ReflexStatus,
    RunInfo,
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
# Per model request: a model slower than this (overloaded) counts as failed and the chain moves
# on, instead of a chat waiting a minute or more for one answer.
REQUEST_TIMEOUT: ModelSettings = {"timeout": 25}
# Reasoning chain only (chat, startup, wake-ups): start the next models in parallel when the
# current attempt has not answered after this long. 0 disables hedging.
HEDGE_AFTER_SECONDS = 8.0
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


class ModelSkipped(ModelAPIError):
    """A chain member the circuit breaker skipped without calling it."""


def is_transient_llm_error(exc: BaseException) -> bool:
    """Worth retrying: overloaded/rate-limited provider or network trouble.
    Not retried: bad key (401/403), unknown model, invalid request, our own bugs, and an
    exhausted quota (429 "check your plan and billing", no Retry-After): waiting won't help.
    A FallbackModel raises a group with one error per model: transient if any of them is."""
    if isinstance(exc, BaseExceptionGroup):
        return any(is_transient_llm_error(e) for e in exc.exceptions)
    if isinstance(exc, ModelSkipped):  # the breaker skipped it: says nothing about the provider
        return False
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
)  # gemini-3-flash removed: it answered 404 on every run (telemetry), costing 1-5 s each time


# Models that failed during the current run (filled by _TimedModel, read by _log_run). Runs are
# serialised by the agent lock, and the context var keeps a stray call from touching another run.
_FAILED_MODELS: ContextVar[list[FallbackInfo] | None] = ContextVar("failed_models", default=None)


def _log_run(
    trigger: DecisionTrigger, seconds: float, result: Any, *, tier: str, mode: str,
    fallbacks: list[FallbackInfo] | None = None,
) -> RunInfo:
    """One line per agent run (duration, model round-trips, models used, tools called) and the
    same figures as the `RunInfo` stored on the Decision. Telemetry must never break a run."""
    try:
        responses = [m for m in result.all_messages() if isinstance(m, ModelResponse)]
    except Exception:  # noqa: BLE001
        responses = []
    tools = [p.tool_name for m in responses for p in m.parts if isinstance(p, ToolCallPart)]
    models = sorted({m.model_name for m in responses if m.model_name})
    try:
        usage = result.usage
        in_tok, out_tok = int(usage.input_tokens or 0) or None, int(usage.output_tokens or 0) or None
    except Exception:  # noqa: BLE001 - a provider that reports no usage
        in_tok = out_tok = None
    log.info("agent run done (%s) in %.1fs: %d model requests via %s, tokens in/out=%s/%s, tools=%s",
             trigger.value, seconds, len(responses), ",".join(models) or "?", in_tok, out_tok, tools)
    return RunInfo(
        inputTokens=in_tok,
        outputTokens=out_tok,
        durationMs=round(seconds * 1000),
        model=",".join(models) or None,
        tier="reasoning" if tier == "reasoning" else "fast",
        mode="full" if mode == "full" else "delta",
        modelRequests=len(responses),
        fallbacks=list(fallbacks or []),
    )


# AGENT_MODEL_REASONING=auto-reasoning: the Flash models first (they reason better: in the eval
# they acted on situations no rule covers), then the Flash Lite ones as a reserve when the small
# Flash quota (5 RPM / 20 RPD each) runs out, so a reasoning run never finds no model.
AUTO_REASONING_MODELS: tuple[str, ...] = (
    "google:gemini-3.8-flash",
    "google:gemini-3.6-flash",
    "google:gemini-3.5-flash-lite",
    "google:gemini-3.1-flash-lite",
)


def model_names(spec: str) -> list[str]:
    """One model, a comma-separated fallback list, `auto` or `auto-reasoning`."""
    key = spec.strip().lower()
    if key == "auto":
        return list(AUTO_MODELS)
    if key == "auto-reasoning":
        return list(AUTO_REASONING_MODELS)
    return [m.strip() for m in spec.split(",") if m.strip()]


# "<provider>:" alone (e.g. AGENT_MODEL_REASONING=groq:) expands to these, best first. Free tiers
# limit each model separately, so every extra model is extra headroom.
DEFAULT_PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
    "openai": ("openai:gpt-5.4-mini", "openai:gpt-5-mini"),
    "groq": ("groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b", "groq:openai/gpt-oss-20b"),
}
GROQ_BASE_URL = "https://api.groq.com/openai/v1"  # OpenAI-compatible endpoint


def expand_spec(spec: str) -> list[str]:
    """model_names, with "<provider>:" alone (or a "<placeholder>") -> that provider's defaults."""
    names: list[str] = []
    for name in model_names(spec):
        provider, sep, model = name.partition(":")
        if sep and (not model or model.startswith("<")):
            names += DEFAULT_PROVIDER_MODELS.get(provider, (name,))
        else:
            names.append(name)
    return names


def _fast_base(settings: Settings, reasoning_order: bool) -> list[str]:
    auto = settings.AGENT_MODEL.strip().lower() == "auto"
    return model_names(("auto-reasoning" if reasoning_order else "auto") if auto
                       else settings.AGENT_MODEL)


def reasoning_names(settings: Settings) -> list[str]:
    """Reasoning chain: AGENT_MODEL_REASONING (or Gemini Flash first when AGENT_MODEL=auto),
    then the whole fast chain as fallback, so a reasoning run always finds a model (e.g. Groq
    rate-limited -> Gemini Flash -> Lite). Duplicates removed, order kept."""
    names = expand_spec(settings.AGENT_MODEL_REASONING) + _fast_base(settings, True)
    return list(dict.fromkeys(names))


def fast_names(settings: Settings) -> list[str]:
    """Fast chain: AGENT_MODEL, then the reasoning provider's models as a last resort, so
    reactions keep working when the free Gemini quota of the day runs out."""
    names = _fast_base(settings, False) + expand_spec(settings.AGENT_MODEL_REASONING)
    return list(dict.fromkeys(names))


def _make_model(name: str) -> Model | str:
    """Groq through its OpenAI-compatible API (the `groq` SDK is not a dependency); the rest
    through pydantic-ai's own inference. Raises when the provider's key is missing."""
    provider, _, model = name.partition(":")
    if provider == "groq" and importlib.util.find_spec("groq") is None:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY not set")
        # No SDK-level retries: a 429 (per-minute limit) moves the FallbackModel to the next
        # model at once instead of waiting inside the client.
        from openai import AsyncOpenAI

        provider_ = OpenAIProvider(openai_client=AsyncOpenAI(
            base_url=GROQ_BASE_URL, api_key=key, max_retries=0, timeout=60))
        profile = OpenAIChatModel(model, provider=provider_).profile
        # Groq rejects (400 tool_use_failed) when gpt-oss answers in text instead of calling the
        # output tool, and refuses JSON mode together with tools (chat). So the decision schema
        # goes in the prompt and comes back as plain-text JSON, validated (and retried) by us.
        return OpenAIChatModel(model, provider=provider_, profile={
            **profile, "default_structured_output_mode": "prompted",
            "supports_json_schema_output": False, "supports_json_object_output": False,
        })
    return infer_model(name)


def build_model(spec: str | list[str], hedge_after: float = 0) -> Model | str:
    """One name -> that model. Several -> FallbackModel: on any API error (503 overloaded,
    429 quota, network, no credit) the next model is tried at once; each model has its own
    quota. Models whose key is missing are left out of the chain (logged); with none left
    (tests, CI) it degrades to the first name, resolved lazily."""
    names = model_names(spec) if isinstance(spec, str) else list(spec)
    if not names:
        return str(spec)
    if len(names) == 1 and not names[0].startswith("groq:"):
        return names[0]
    models: list[Model | str] = []
    for name in names:
        try:
            models.append(_make_model(name))
        except Exception as e:  # missing API key / SDK: skip it, keep the rest of the chain
            log.warning("model %s left out of the chain (%s)", name, e)
    if not models:
        return names[0]
    if len(models) == 1:
        return models[0]
    last = len(models) - 1
    timed = [_TimedModel(m, skippable=i < last) for i, m in enumerate(models)]
    if hedge_after and len(timed) >= 3:
        return HedgedFallbackModel(*timed, hedge_after=hedge_after)
    return FallbackModel(*timed)


# Models already tried (or in flight) in the current hedged request, shared by both racers, so
# no model is called twice for the same request. None outside a hedged request.
_CLAIMED: ContextVar[set[str] | None] = ContextVar("claimed_models", default=None)


class HedgedFallbackModel(FallbackModel):
    """FallbackModel that does not wait for a slow model to fail. The chain starts as usual; when
    the current attempt has not answered after `hedge_after` seconds, a second walk of the chain
    is started in parallel and the first answer wins, the other request is cancelled. The two
    walks share a set of claimed models (see `_TimedModel`): each model is called at most once
    per request, so the hedge picks up at the first model nobody has tried yet and the primary
    skips what the hedge took. It costs extra quota only in the slow case, and a slow model is
    exactly where the sequential chain loses 25 s on the request timeout."""

    def __init__(self, *models: Model, hedge_after: float) -> None:
        super().__init__(*models)
        self._hedge_after = hedge_after

    async def request(self, messages: Any, model_settings: Any, model_request_parameters: Any
                      ) -> ModelResponse:
        token = _CLAIMED.set(set())  # both tasks below copy this context, so share the set
        try:
            return await self._race(messages, model_settings, model_request_parameters)
        finally:
            _CLAIMED.reset(token)

    async def _race(self, messages: Any, model_settings: Any, model_request_parameters: Any
                    ) -> ModelResponse:
        primary = asyncio.ensure_future(
            super().request(messages, model_settings, model_request_parameters))
        done, _ = await asyncio.wait({primary}, timeout=self._hedge_after)
        if done:
            return primary.result()
        log.info("chain attempt still running after %.0fs: hedging with the next models",
                 self._hedge_after)
        hedge = asyncio.ensure_future(
            super().request(messages, model_settings, model_request_parameters))
        pending = {primary, hedge}
        first_error: BaseException | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() is None:
                        return task.result()
                    first_error = first_error or task.exception()
            assert first_error is not None
            raise first_error
        finally:
            for task in (primary, hedge):
                if not task.done():
                    task.cancel()


# Circuit breaker: a model that just failed for a reason that will not go away in seconds is
# skipped (instantly, no network) instead of costing the next run its full failure time again.
# Seconds to skip by HTTP status; anything else (timeout, 503, network) gets the short default.
BREAKER_SECONDS: dict[int, float] = {401: 3600, 403: 3600, 404: 3600, 400: 300, 413: 1800, 429: 60}
BREAKER_DEFAULT_SECONDS = 30.0
_BREAKER: dict[str, float] = {}  # model name -> time.monotonic() until which it is skipped


def breaker_open(name: str) -> bool:
    until = _BREAKER.get(name)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _BREAKER[name]
        return False
    return True


def breaker_trip(name: str, exc: BaseException) -> None:
    status = getattr(exc, "status_code", None)
    seconds = BREAKER_SECONDS.get(status, BREAKER_DEFAULT_SECONDS) if status else \
        BREAKER_DEFAULT_SECONDS
    if status == 429:
        seconds = max(seconds, _retry_after(exc) or 0)
    _BREAKER[name] = time.monotonic() + seconds


class _TimedModel(WrapperModel):
    """A chain member that logs its failures with how long they took, so a slow fallback (which
    model failed, why, how long it cost) is visible in the backend log. With `skippable`, a model
    that failed recently is skipped through the circuit breaker; the last model of a chain is
    never skippable, so a chain always tries at least one model."""

    def __init__(self, model: Model | str, skippable: bool = False) -> None:
        super().__init__(infer_model(model) if isinstance(model, str) else model)
        self._skippable = skippable

    async def request(self, messages: Any, model_settings: Any, params: Any) -> ModelResponse:
        if self._skippable and breaker_open(self.model_name):
            raise ModelSkipped(self.model_name, "skipped: failed recently (circuit breaker)")
        claimed = _CLAIMED.get()
        if claimed is not None:  # hedged request: the other racer already took this model
            if self.model_name in claimed:
                raise ModelSkipped(self.model_name, "skipped: already tried for this request")
            claimed.add(self.model_name)
        started = time.monotonic()
        try:
            return await super().request(messages, model_settings, params)
        except Exception as e:
            breaker_trip(self.model_name, e)
            elapsed = time.monotonic() - started
            log.info("model %s failed after %.1fs: %s %s", self.model_name,
                     elapsed, type(e).__name__, str(e)[:160])
            failed = _FAILED_MODELS.get()
            if failed is not None:
                status = getattr(e, "status_code", None)
                failed.append(FallbackInfo(
                    model=self.model_name,
                    error=f"{type(e).__name__}{f' {status}' if status else ''}",
                    ms=round(elapsed * 1000),
                ))
            raise


class WakeUp(BaseModel):
    at: str = Field(description='House local time "HH:MM" (next occurrence) or ISO datetime')
    reason: str


class AgentOutput(BaseModel):
    """What the LLM returns. Ids, timestamps and trigger are filled in by code."""

    explanation: str = Field(description="Short user-facing explanation in PT-PT; describes only the actions you return")
    inferredRoom: str | None = None
    inferredActivity: str | None = None
    actions: list[ProposedAction] = []
    confidence: float = Field(default=0.5, ge=0, le=1)
    beliefState: str = Field(description="Your updated belief state in PT-PT, <= ~800 chars")
    insightUpdates: list[InsightUpdate] = []
    reflexProposals: list[ReflexDraft] = []
    reflexUpdates: list[ReflexUpdateDraft] = []
    wakeUps: list[WakeUp] = []


class Clock:
    """The house clock: real time in the house's time zone, or NOW_OVERRIDE (house local time)
    advancing in real time from process start (demos).

    Without `tz` (tests, legacy): real UTC, and a naive NOW_OVERRIDE stays naive."""

    def __init__(self, override: str | None, tz: str | None = None) -> None:
        self.tz = ZoneInfo(tz) if tz else None
        self._start = datetime.now(UTC)
        base = datetime.fromisoformat(override) if override else None
        if base is not None and base.tzinfo is None and self.tz is not None:
            base = base.replace(tzinfo=self.tz)
        self._base = base

    def now(self) -> datetime:
        if self._base is None:
            return datetime.now(self.tz or UTC)
        return self._base + (datetime.now(UTC) - self._start)


DAILY_REVIEW_HOUR = 4  # house time; quiet hour, before morning routines
DAILY_REVIEW_KEY = "agent.daily_review"
DAILY_REVIEW = "Daily routine review."
ROUTINE_REVIEW = (
    "Look at the plugs' routine stats below. For a consistent routine create (or reinforce) the "
    "insight and propose a scheduled reflex for it in this same decision; then answer."
)
MAX_WAKE_AHEAD = timedelta(hours=24)
STANDING_ALERT_AGE = timedelta(minutes=10)  # an "alarm" reading older than this is not news


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"
WAKE_GRACE = timedelta(minutes=10)


class WakeScheduler:
    """Timers the agent asks for (`AgentOutput.wakeUps`): nothing changes in the house at 07:25,
    so without them a time-based routine (S3) could never be acted on."""

    def __init__(self, clock: Clock, wake: Callable[[str], Awaitable[None]]) -> None:
        self._clock = clock
        self._wake = wake
        self._timers: dict[str, tuple[datetime, str, asyncio.Task[None]]] = {}

    def resolve(self, at: str) -> datetime | None:
        """"HH:MM" -> next occurrence; ISO -> as is. Same tz-awareness as the clock.
        A "HH:MM" that passed less than WAKE_GRACE ago means "now" (the agent was a bit late,
        e.g. started at 07:24 and asked for 07:20), not tomorrow."""
        now = self._clock.now()
        try:
            if len(at) <= 5 and ":" in at:
                h, m = (int(x) for x in at.split(":"))
                when = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if when > now:
                    return when
                return now if now - when <= WAKE_GRACE else when + timedelta(days=1)
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
        reasoning_model: Model | str | None = None,
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
        self.clock = clock or Clock(settings.NOW_OVERRIDE, settings.TIMEZONE)
        # Two tiers: `fast` for everyday reactions, `reasoning` for chat, startup/daily review and
        # wake-ups. Tests may inject one model (both tiers) or both.
        fast = model or build_model(fast_names(settings))
        reasoning = reasoning_model or model or build_model(
            reasoning_names(settings), hedge_after=HEDGE_AFTER_SECONDS)
        self._model_name = (
            f"{model}" if model and not reasoning_model else
            f"fast={','.join(fast_names(settings))}; "
            f"reasoning={','.join(reasoning_names(settings))}"
        )
        text = instructions(settings.REFLEX_MIN_CONFIDENCE)
        # Chat: reasoning model with the read-only tools.
        self._agent: Agent[AgentDeps, AgentOutput] = Agent(
            reasoning, deps_type=AgentDeps, output_type=AgentOutput, instructions=text,
            defer_model_check=True, retries=2, model_settings=REQUEST_TIMEOUT,
        )
        register_tools(self._agent)
        # No tools (everything is in the prompt): plain reactions on the fast model...
        self._final_agent: Agent[None, AgentOutput] = Agent(
            fast, output_type=AgentOutput, instructions=text, defer_model_check=True, retries=1,
            model_settings=REQUEST_TIMEOUT,
        )
        # ...and automatic runs that need thinking (startup, daily review, wake-ups), plus the
        # forced answer after a chat that used up its tool calls.
        self._reasoning_agent: Agent[None, AgentOutput] = Agent(
            reasoning, output_type=AgentOutput, instructions=text, defer_model_check=True,
            model_settings=REQUEST_TIMEOUT,
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
        """First run after the initial poll: full snapshot and a routine review (which also counts
        as today's daily review, so a restart costs one LLM call, not two)."""
        self._startup = True
        now = self.clock.now()
        if now.hour >= DAILY_REVIEW_HOUR:
            await self._repo.kv_set(DAILY_REVIEW_KEY, now.date().isoformat())
        await self.wake("The backend just started. " + ROUTINE_REVIEW)

    async def maybe_daily_review(self) -> bool:
        """Once per house day (from DAILY_REVIEW_HOUR): re-check routines with fresh stats, so a
        backend that runs for weeks keeps learning, not only at startup (persisted in SQLite)."""
        now = self.clock.now()
        if now.hour < DAILY_REVIEW_HOUR:
            return False
        today = now.date().isoformat()
        if await self._repo.kv_get(DAILY_REVIEW_KEY) == today:
            return False
        await self._repo.kv_set(DAILY_REVIEW_KEY, today)
        await self.wake(f"{DAILY_REVIEW} {ROUTINE_REVIEW}")
        return True

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
                standing_alerts=await self._standing_alerts(devices),
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
            # Reasoning tier: chat, startup, and every wake-up (door check, schedules, daily
            # review): few per day, and where thinking matters. Plain reactions stay fast.
            reasoning = trigger != DecisionTrigger.state_change or bool(wake_reasons)
            if not use_tools:
                prompt += NO_TOOLS_NOTE
            failed_models: list[FallbackInfo] = []
            _FAILED_MODELS.set(failed_models)
            started = time.monotonic()
            log.info("agent run started (%s, %s mode, %d changes, tools=%s, tier=%s)",
                     trigger.value, run_input.mode, len(changes), use_tools,
                     "reasoning" if reasoning else "fast")
            try:
                result = await self._run_llm(prompt, deps, use_tools=use_tools,
                                             reasoning=reasoning)
            except BaseException as e:
                log.info("agent run failed after %.1fs: %s", time.monotonic() - started,
                         type(e).__name__)
                self._wake_reasons = wake_reasons + self._wake_reasons  # retry them next run
                raise
            run_info = _log_run(trigger, time.monotonic() - started, result,
                                tier="reasoning" if reasoning else "fast", mode=run_input.mode,
                                fallbacks=failed_models)
            decision = await self._finish(result.output, trigger, run_input, run_info)
            self._last_cutoff = cutoff
            return decision

    async def _standing_alerts(self, devices: list) -> list[str]:
        """Alert readings ("alarm") that are NOT new: they have not changed for a while, or have
        never changed while the house has been observed. Raw facts, so the agent can tell a
        long-standing reading (that device's normal state here) from a real event."""
        out: list[str] = []
        now = datetime.now(UTC)
        observed_since = await self._repo.first_snapshot_at()
        for d in devices:
            for c in d.capabilities:
                if str(c.value).lower() != "alarm":
                    continue
                last = await self._repo.last_change_at(d.id, c.type)
                if last is not None and now - last < STANDING_ALERT_AGE:
                    continue  # recent: a real event, leave it to the changes
                name = d.alias or d.name or d.id
                if last is not None:
                    out.append(f"{name} ({d.id}) {c.type}='alarm' unchanged for "
                               f"{_age(now - last)}")
                else:
                    since = _age(now - observed_since) if observed_since else "the start"
                    out.append(f"{name} ({d.id}) {c.type}='alarm' since observation started "
                               f"({since} ago), never changed")
        return out

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
        routine_wake = any("Scheduled wake-up" in w or DAILY_REVIEW in w for w in wake_reasons)
        if trigger == DecisionTrigger.startup or routine_wake:
            routines = []
            for d in devices:
                if d.enabled and d.type == "plug":
                    stats = await compute_routine_stats(
                        self._repo, d.id, "instantPower", tz=self.clock.now().tzinfo
                    )
                    stats.pop("byDay", None)  # the summaries are enough; keeps the prompt small
                    routines.append({"name": d.alias or d.name, **stats})
        return presence, routines

    async def _run_llm(
        self, prompt: str, deps: AgentDeps, use_tools: bool = True, reasoning: bool = True
    ) -> Any:
        """agent.run with retries on transient provider errors (503 overloaded, 429, network).
        Runs stay serialised meanwhile; changes arriving during the wait set the scheduler's
        dirty flag and are picked up by the next run (D5).

        `use_tools=False`: the prompt already carries what the run needs, so one request
        decides, on the reasoning model or (`reasoning=False`, plain reactions) the fast one.
        Tool runs (chat) always use the reasoning model. Each model is a fallback chain."""
        attempts = len(self.retry_delays) + 1
        for attempt in range(1, attempts + 1):
            try:
                if not use_tools:
                    no_tools = self._reasoning_agent if reasoning else self._final_agent
                    return await no_tools.run(prompt, usage_limits=UsageLimits(request_limit=2))
                try:
                    return await self._agent.run(
                        prompt, deps=deps,
                        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
                    )
                except UsageLimitExceeded:
                    log.warning("agent used %d model requests without deciding; asking for the "
                                "decision now, without tools", MAX_MODEL_REQUESTS)
                    return await self._reasoning_agent.run(
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
        self,
        out: AgentOutput,
        trigger: DecisionTrigger,
        run_input: AgentRunInput,
        run_info: RunInfo | None = None,
    ) -> Decision:
        belief = await self._belief.save(out.beliefState, run_input.mode, datetime.now(UTC))
        decision_id = uuid.uuid4().hex
        # Actions on devices that do not exist (an invented "gateway") are dropped, not shown to
        # the user as failed suggestions.
        known = {d.id for d in await self._registry.list_devices()}
        invented = [a.deviceId for a in out.actions if a.deviceId not in known]
        if invented:
            log.warning("decision %s: dropped actions on unknown devices %s", decision_id, invented)
            out.actions = [a for a in out.actions if a.deviceId in known]
        room_names = {room.id: room.name for room in run_input.rooms or []}
        touched = await self._insights.apply(out.insightUpdates, decision_id)
        proposals = await self._reflexes.accept_proposals(
            out.reflexProposals,
            from_user=trigger == DecisionTrigger.chat,  # the user asked: no insight needed
            new_insight_ids=[i.id for i in touched],  # link to the insight created just now
        )
        reflex_updates = await self._reflexes.apply_agent_updates(out.reflexUpdates)
        decision = Decision(
            id=decision_id,
            createdAt=datetime.now(UTC),
            trigger=trigger,
            explanation=out.explanation,
            inferredRoom=room_names.get(out.inferredRoom or "", out.inferredRoom),
            inferredActivity=out.inferredActivity,
            actions=out.actions,
            confidence=out.confidence,
            beliefState=belief,
            insightUpdates=out.insightUpdates,
            reflexProposals=proposals,
            reflexUpdates=reflex_updates,
            runInfo=run_info,
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

    async def create_reflex(self, body: ReflexCreate) -> Reflex:
        return await self._reflexes.create_user_reflex(body)

    async def confirm_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.proposed}, ReflexStatus.active)

    async def pause_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.active}, ReflexStatus.paused)

    async def resume_reflex(self, reflex_id: str) -> Reflex:
        return await self._reflexes.move(reflex_id, {ReflexStatus.paused}, ReflexStatus.active)

    async def delete_reflex(self, reflex_id: str) -> None:
        await self._reflexes.move(reflex_id, set(ReflexStatus), ReflexStatus.deleted)

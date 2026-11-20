"""Latency work: circuit breaker, hedged fallback, token telemetry, stable prompt prefix.

# Owner: Team A
"""
import asyncio
import time
from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.agent import agent as agent_mod
from app.agent.agent import (
    AUTO_MODELS,
    AUTO_REASONING_MODELS,
    HedgedFallbackModel,
    _log_run,
    _TimedModel,
    breaker_open,
)
from app.agent.prompts import build_prompt
from app.schemas import AgentRunInput, DecisionTrigger, Insight, Room


@pytest.fixture(autouse=True)
def clean_breaker():
    agent_mod._BREAKER.clear()
    yield
    agent_mod._BREAKER.clear()


def fm(name: str, behaviour):
    return FunctionModel(behaviour, model_name=name)


def ok(text: str, delay: float = 0):
    async def run(messages, info):
        if delay:
            await asyncio.sleep(delay)
        return ModelResponse(parts=[TextPart(text)])
    return run


def fail(status: int, calls: list | None = None):
    async def run(messages, info):
        if calls is not None:
            calls.append(1)
        raise ModelHTTPError(status, "m", {})
    return run


async def ask(model) -> str:
    return (await Agent(model, output_type=str).run("hi")).output


def test_dead_model_is_gone_from_the_chains():
    assert not any("gemini-3-flash" == n.split(":")[-1] for n in AUTO_MODELS + AUTO_REASONING_MODELS)


async def test_breaker_skips_a_model_that_just_failed():
    calls: list[int] = []
    from pydantic_ai.models.fallback import FallbackModel
    chain = FallbackModel(_TimedModel(fm("dead", fail(404, calls)), skippable=True),
                          _TimedModel(fm("good", ok("yes"))))
    assert await ask(chain) == "yes"
    assert breaker_open("dead") and len(calls) == 1
    assert await ask(chain) == "yes"
    assert len(calls) == 1  # second run skipped the dead model without calling it


async def test_last_model_is_never_skipped():
    agent_mod._BREAKER["only"] = time.monotonic() + 100
    m = _TimedModel(fm("only", ok("still answers")), skippable=False)
    assert await ask(m) == "still answers"


async def test_skipped_model_raises_api_error_for_the_fallback():
    agent_mod._BREAKER["x"] = time.monotonic() + 100
    m = _TimedModel(fm("x", ok("no")), skippable=True)
    with pytest.raises(ModelAPIError):
        await ask(m)


async def test_hedge_answers_before_the_slow_first_model_finishes():
    slow = _TimedModel(fm("slow", ok("slow", delay=5)), skippable=True)
    a = _TimedModel(fm("a", ok("hedged")), skippable=True)
    b = _TimedModel(fm("b", ok("b")))
    started = time.monotonic()
    out = await ask(HedgedFallbackModel(slow, a, b, hedge_after=0.2))
    assert out == "hedged" and time.monotonic() - started < 2


async def test_hedge_not_started_when_first_model_is_fast():
    calls: list[int] = []
    first = _TimedModel(fm("first", ok("first")), skippable=True)
    a = _TimedModel(fm("a", fail(500, calls)), skippable=True)
    b = _TimedModel(fm("b", ok("b")))
    assert await ask(HedgedFallbackModel(first, a, b, hedge_after=1)) == "first"
    assert calls == []


async def test_hedge_still_falls_back_when_everything_fails_fast():
    a = _TimedModel(fm("a", fail(500)), skippable=True)
    b = _TimedModel(fm("b", fail(500)), skippable=True)
    c = _TimedModel(fm("c", ok("last")))
    assert await ask(HedgedFallbackModel(a, b, c, hedge_after=5)) == "last"


class _Usage:
    input_tokens = 3200
    output_tokens = 410


class _Result:
    usage = _Usage()

    def all_messages(self):
        return []


def test_run_info_carries_tokens():
    info = _log_run(DecisionTrigger.chat, 1.0, _Result(), tier="reasoning", mode="full")
    assert (info.inputTokens, info.outputTokens) == (3200, 410)


def test_run_info_tokens_absent_when_provider_reports_none():
    info = _log_run(DecisionTrigger.chat, 1.0, object(), tier="fast", mode="delta")
    assert info.inputTokens is None and info.outputTokens is None


def test_stable_blocks_come_before_volatile_ones():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    insight = Insight(id="i1", statement="TV a noite", confidence=0.7, createdAt=now, lastConfirmedAt=now)
    base = dict(rooms=[Room(id="r1", name="Sala")], insights=[insight])
    p1 = build_prompt(AgentRunInput(mode="delta", now=now, beliefState="A", **base))
    p2 = build_prompt(AgentRunInput(mode="delta", now=now.replace(minute=5), beliefState="B", **base))
    assert p1.startswith("Rooms:") and p1.index("Rooms:") < p1.index("Mode:")
    common = len(__import__("os").path.commonprefix([p1, p2]))
    assert common > p1.index("Mode:")  # the shared prefix covers the stable blocks


def test_breaker_skip_is_not_a_transient_error():
    from app.agent.agent import ModelSkipped, is_transient_llm_error
    skipped = ModelSkipped("m", "skipped")
    assert not is_transient_llm_error(skipped)
    # A chain that failed only on permanent errors + skips must not be retried
    group = BaseExceptionGroup("chain", [skipped, ModelHTTPError(400, "m", {})])
    assert not is_transient_llm_error(group)
    # ...but a real transient failure in the chain still is
    assert is_transient_llm_error(BaseExceptionGroup("chain", [skipped, ModelHTTPError(503, "m", {})]))


async def test_skipped_model_raises_model_skipped():
    from app.agent.agent import ModelSkipped
    agent_mod._BREAKER["y"] = time.monotonic() + 100
    with pytest.raises(ModelSkipped):
        await ask(_TimedModel(fm("y", ok("no")), skippable=True))


async def test_hedge_never_calls_the_same_model_twice():
    """Regression: the hedge walked the chain again and hit the model the primary was waiting on."""
    calls: dict[str, int] = {"m1": 0, "m2": 0, "m3": 0}

    def counted(name: str, behaviour):
        async def run(messages, info):
            calls[name] += 1
            return await behaviour(messages, info)
        return run

    async def slow_503(messages, info):
        await asyncio.sleep(1)
        raise ModelHTTPError(503, "m2", {})

    m1 = _TimedModel(fm("m1", counted("m1", fail(500))), skippable=True)
    m2 = _TimedModel(fm("m2", counted("m2", slow_503)), skippable=True)
    m3 = _TimedModel(fm("m3", counted("m3", ok("answer"))))
    assert await ask(HedgedFallbackModel(m1, m2, m3, hedge_after=0.2)) == "answer"
    assert calls == {"m1": 1, "m2": 1, "m3": 1}

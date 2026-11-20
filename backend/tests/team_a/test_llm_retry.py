"""Team A: the agent retries transient model-provider failures (503 overloaded, 429, network)."""
from __future__ import annotations

import json
import os

from pydantic_ai.exceptions import ModelHTTPError, ModelQuotaExceededError, ModelUnavailableError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server, sim_devices

from app.agent.agent import (
    AUTO_MODELS,
    MAX_MODEL_REQUESTS,
    build_model,
    is_transient_llm_error,
    model_names,
)
from app.agent.wiring import build_services, export_agent_api_key
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import DeviceSource


def test_transient_classification():
    assert is_transient_llm_error(ModelUnavailableError("m", "503 high demand"))
    assert is_transient_llm_error(ModelHTTPError(429, "m"))
    assert is_transient_llm_error(ModelHTTPError(503, "m"))
    assert is_transient_llm_error(TimeoutError())
    assert not is_transient_llm_error(ModelHTTPError(401, "m"))  # bad key: fail fast
    assert not is_transient_llm_error(ModelHTTPError(400, "m"))  # bad request / unknown model
    assert not is_transient_llm_error(ValueError("our bug"))
    # 429 quota: a per-minute limit (Retry-After) is worth waiting for, an exhausted plan is not
    assert is_transient_llm_error(ModelQuotaExceededError("m", "rate", retry_after=30))
    assert not is_transient_llm_error(ModelQuotaExceededError("m", "check your plan and billing"))


class Flaky:
    """Fails `fails` times with `exc`, then returns a no-op decision."""

    def __init__(self, fails: int, exc: Exception) -> None:
        self.fails, self.exc, self.calls = fails, exc, 0

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        out = {"explanation": "Nada a fazer.", "beliefState": "ok", "actions": []}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


async def run_once(flaky: Flaky, model=None):
    srv = fake_server(sim_devices(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="")
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=model or FunctionModel(flaky)
    )
    services.scheduler._min_gap = 0
    services.agent.retry_delays = (0, 0, 0)
    try:
        await services.poller.poll_once(DeviceSource.sim)
        srv.state["tv1"]["capabilities"][0]["value"] = "on"
        await services.poller.poll_once(DeviceSource.sim)
        await services.scheduler.wait_idle()
        return await services.actions.list_decisions(5), services
    finally:
        await services.aclose()


async def test_overloaded_provider_is_retried_until_it_answers():
    flaky = Flaky(2, ModelUnavailableError("gemini", "503 UNAVAILABLE high demand"))
    decisions, _ = await run_once(flaky)
    assert flaky.calls == 3 and len(decisions) == 1


async def test_bad_key_is_not_retried():
    flaky = Flaky(5, ModelHTTPError(401, "gemini"))
    decisions, _ = await run_once(flaky)
    assert flaky.calls == 1 and decisions == []


async def test_gives_up_after_the_last_retry_and_keeps_the_changes():
    flaky = Flaky(99, ModelUnavailableError("gemini", "503"))
    decisions, services = await run_once(flaky)
    assert flaky.calls == 4 and decisions == []  # 1 attempt + 3 retries
    # The cutoff did not advance: the next run still sees the TV change.
    assert services.agent._last_cutoff is None


# --- AGENT_MODEL: one model, a fallback list, or `auto` ---------------------------------------


def test_model_spec_parsing():
    assert model_names("google:gemini-3.8-flash") == ["google:gemini-3.8-flash"]
    assert model_names(" google:a , google:b ") == ["google:a", "google:b"]
    assert model_names("auto") == list(AUTO_MODELS)
    assert AUTO_MODELS[0].endswith("flash-lite")  # most daily headroom first
    assert build_model("google:gemini-3.8-flash") == "google:gemini-3.8-flash"  # stays lazy
    assert isinstance(build_model("test,test"), FallbackModel)


async def test_fallback_model_takes_over_when_the_first_is_overloaded():
    overloaded = Flaky(99, ModelUnavailableError("gemini-3.8-flash", "503 high demand"))
    backup = Flaky(0, RuntimeError("unused"))
    decisions, _ = await run_once(
        overloaded, model=FallbackModel(FunctionModel(overloaded), FunctionModel(backup))
    )
    assert len(decisions) == 1
    assert overloaded.calls == 1 and backup.calls == 1  # switched at once, no retry wait


async def test_whole_chain_failing_transiently_is_retried():
    a = Flaky(1, ModelUnavailableError("a", "503"))
    b = Flaky(1, ModelQuotaExceededError("b", "quota", retry_after=1))
    decisions, _ = await run_once(a, model=FallbackModel(FunctionModel(a), FunctionModel(b)))
    assert len(decisions) == 1 and a.calls == 2  # group was transient -> second pass, `a` answers


def test_exported_key_for_auto(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert export_agent_api_key(Settings(_env_file=None, AGENT_MODEL="auto", AGENT_API_KEY="k")) == "GEMINI_API_KEY"
    assert os.environ["GEMINI_API_KEY"] == "k"


# --- a model that loops on tools still ends with a decision -----------------------------------


class ToolLooper:
    """Calls get_home_state forever while tools exist; decides once none are offered."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        for m in messages:
            for p in getattr(m, "parts", []):
                if getattr(p, "part_kind", "") == "user-prompt" and isinstance(p.content, str):
                    self.prompts.append(p.content)
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart("get_home_state", {})])
        out = {"explanation": "Baixei a luz porque a TV ligou.", "beliefState": "tv",
               "actions": [{"deviceId": "lamp1", "update": {"type": "brightness", "value": 30}}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


async def test_tool_loop_in_chat_is_cut_and_the_agent_still_decides():
    """Only chat runs have tools; a model that loops on them is cut and forced to answer."""
    from app.schemas import ChatRequest

    looper = ToolLooper()
    srv = fake_server(sim_devices(), with_sim_tools=True)
    services = await build_services(
        Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL=""),
        clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(looper),
    )
    try:
        await services.poller.poll_once(DeviceSource.sim)
        resp = await services.agent.chat(ChatRequest(message="baixa a luz"))
        assert resp.decision is not None and resp.decision.actions[0].update.value == 30
        assert looper.calls == MAX_MODEL_REQUESTS + 1  # capped run + one forced answer
        assert "No tools are available now" in looper.prompts[-1]
        # plugin starterAutomations reach the prompt as expectedBehaviours
        assert "expectedBehaviours" in looper.prompts[0] and "Baixar a luz" in looper.prompts[0]
    finally:
        await services.aclose()


async def test_plain_delta_run_is_one_request_without_tools():
    looper = ToolLooper()
    srv = fake_server(sim_devices(), with_sim_tools=True)
    services = await build_services(
        Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL=""),
        clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(looper),
    )
    services.scheduler._min_gap = 0
    try:
        await services.poller.poll_once(DeviceSource.sim)
        srv.state["tv1"]["capabilities"][0]["value"] = "on"
        await services.poller.poll_once(DeviceSource.sim)
        await services.scheduler.wait_idle()  # 1st run: full mode, tools, capped + forced
        first = looper.calls
        srv.state["tv1"]["capabilities"][1]["value"] = 33
        await services.poller.poll_once(DeviceSource.sim)
        await services.scheduler.wait_idle()  # 2nd run: plain delta
        assert looper.calls == first + 1
        assert "quick reaction run: no tools are available" in looper.prompts[-1]
    finally:
        await services.aclose()

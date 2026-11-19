"""Team A: the agent retries transient model-provider failures (503 overloaded, 429, network)."""
from __future__ import annotations

import json

from pydantic_ai.exceptions import ModelHTTPError, ModelUnavailableError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server, sim_devices

from app.agent.agent import is_transient_llm_error
from app.agent.wiring import build_services
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


async def run_once(flaky: Flaky):
    srv = fake_server(sim_devices(), with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="")
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(flaky)
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

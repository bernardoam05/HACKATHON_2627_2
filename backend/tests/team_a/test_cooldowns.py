"""Team A: device blocking after a user override (D9) and the user's options about it."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_devices_poller import fake_server

from app.agent.feedback import FeedbackService
from app.agent.wiring import build_services
from app.config import Settings
from app.mcp_clients.sim import SimClient
from app.schemas import (
    ActionStatus,
    CapabilityUpdate,
    CooldownReason,
    CooldownUpdate,
    DeviceSource,
    ProposedAction,
)


def noop_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    out = {"explanation": "Nada a fazer.", "beliefState": "b", "actions": []}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out))])


class Recorder:
    def __init__(self) -> None:
        self.msgs: list[BaseModel] = []

    async def __call__(self, msg: BaseModel) -> None:
        self.msgs.append(msg)

    def types(self) -> list[str]:
        return [m.type for m in self.msgs]  # type: ignore[attr-defined]


@pytest.fixture
async def home():
    srv = fake_server([
        {"id": "lamp", "name": "Candeeiro", "type": "bulb",
         "capabilities": [{"type": "power", "value": "on", "availableValues": ["on", "off"]},
                          {"type": "brightness", "value": 100, "min": 1, "max": 100}]},
    ], with_sim_tools=True)
    settings = Settings(DB_PATH=":memory:", ALTICE_MCP_URL="", SIM_MCP_URL="",
                        OVERRIDE_COOLDOWN_MINUTES=20)
    services = await build_services(
        settings, clients={DeviceSource.sim: SimClient(srv)}, model=FunctionModel(noop_model)
    )
    services.scheduler._min_gap = 0
    rec = Recorder()
    services.feedback._broadcast = rec
    await services.poller.poll_once(DeviceSource.sim)
    yield services, rec
    await services.aclose()


async def agent_dims_then_user_overrides(services, value: int = 90):
    executor = services.agent._executor
    action = await executor.apply(
        "d1", ProposedAction(deviceId="lamp", update=CapabilityUpdate(type="brightness", value=30)))
    await services.devices.set_capability("lamp", CapabilityUpdate(type="brightness", value=value))
    return action


async def test_override_is_logged_and_user_is_told(home):
    services, rec = home
    action = await agent_dims_then_user_overrides(services)

    # Logged: feedback, the agent's action marked as reverted, and a notice for the UI
    [fb] = await services.repo.recent_feedback(5)
    assert fb.kind == "manual_override" and fb.actionId == action.id
    assert (await services.repo.get_action(action.id)).status == ActionStatus.undone
    assert rec.types() == ["action_updated", "cooldown_started"]
    cd = rec.msgs[-1].cooldown
    assert cd.reason == CooldownReason.manual_override and cd.actionId == action.id
    assert cd.note == ("Mudei brightness para 30, mas alteraste para 90. "
                       "Não mexo neste dispositivo durante 20 min.")
    assert cd.defaultMinutes == 20
    assert [c.deviceId for c in await services.cooldowns.list_cooldowns()] == ["lamp"]

    # The agent and reflexes leave it alone
    skipped = await services.agent._executor.apply(
        "d2", ProposedAction(deviceId="lamp", update=CapabilityUpdate(type="brightness", value=10)))
    assert skipped is None


async def test_user_shortens_block_and_it_expires(home):
    services, rec = home
    await agent_dims_then_user_overrides(services)
    shorter = await services.cooldowns.update_cooldown("lamp", CooldownUpdate(minutes=0.002))
    assert shorter.until is not None and services.feedback.in_cooldown("lamp")
    await asyncio.sleep(0.3)
    assert not services.feedback.in_cooldown("lamp")
    assert rec.msgs[-1].type == "cooldown_ended" and rec.msgs[-1].reason == "expired"
    assert await services.cooldowns.list_cooldowns() == []


async def test_user_blocks_until_unblocked_then_unblocks(home):
    services, rec = home
    await agent_dims_then_user_overrides(services)
    forever = await services.cooldowns.update_cooldown("lamp", CooldownUpdate(untilUnblocked=True))
    assert forever.until is None and forever.reason == CooldownReason.manual_override
    assert services.feedback.in_cooldown("lamp")
    await services.cooldowns.clear_cooldown("lamp")
    assert not services.feedback.in_cooldown("lamp")
    assert rec.msgs[-1].type == "cooldown_ended" and rec.msgs[-1].reason == "user"
    await services.cooldowns.clear_cooldown("lamp")  # idempotent, no second event
    assert rec.types().count("cooldown_ended") == 1


async def test_remember_never_block_this_device(home):
    services, rec = home
    await agent_dims_then_user_overrides(services)
    ended = await services.cooldowns.update_cooldown(
        "lamp", CooldownUpdate(minutes=0, rememberForDevice=True))
    assert ended.defaultMinutes == 0 and not services.feedback.in_cooldown("lamp")

    await agent_dims_then_user_overrides(services, value=80)  # second override
    assert not services.feedback.in_cooldown("lamp")  # still logged, but no block
    assert [f.kind for f in await services.repo.recent_feedback(5)] == ["manual_override"] * 2
    assert rec.types().count("cooldown_started") == 1


async def test_remember_shorter_default_for_this_device(home):
    services, rec = home
    await agent_dims_then_user_overrides(services)
    await services.cooldowns.update_cooldown("lamp", CooldownUpdate(minutes=5, rememberForDevice=True))
    await services.cooldowns.clear_cooldown("lamp")
    await agent_dims_then_user_overrides(services, value=70)
    [cd] = await services.cooldowns.list_cooldowns()
    assert cd.defaultMinutes == 5 and cd.note.endswith("durante 5 min.")


async def test_user_can_block_a_device_without_override(home):
    services, _ = home
    cd = await services.cooldowns.update_cooldown("lamp", CooldownUpdate(minutes=60))
    assert cd.reason == CooldownReason.user and services.feedback.in_cooldown("lamp")
    with pytest.raises(KeyError):  # -> 404
        await services.cooldowns.update_cooldown("nope", CooldownUpdate(minutes=5))


async def test_blocks_and_defaults_survive_restart(home):
    services, _ = home
    await agent_dims_then_user_overrides(services)
    await services.cooldowns.update_cooldown("lamp", CooldownUpdate(untilUnblocked=True))
    await services.cooldowns.update_cooldown("lamp", CooldownUpdate(minutes=30, rememberForDevice=True))
    restarted = FeedbackService(services.repo)
    await restarted.load()
    assert restarted.in_cooldown("lamp") and restarted.default_minutes("lamp") == 30
    restarted.close()


async def test_undo_also_blocks_with_its_own_note(home):
    services, rec = home
    action = await services.agent._executor.apply(
        "d1", ProposedAction(deviceId="lamp", update=CapabilityUpdate(type="brightness", value=30)))
    await services.actions.undo(action.id)
    assert rec.msgs[-1].type == "cooldown_started"
    assert rec.msgs[-1].cooldown.reason == CooldownReason.undo
    [active] = await services.cooldowns.list_cooldowns()
    assert active.reason == CooldownReason.undo
    assert active.note.startswith("Anulaste a minha ação (brightness = 30).")

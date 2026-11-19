"""Chat, decisions, actions, cooldowns, history, insights, reflexes. (Shared contract)"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.schemas import (
    ActionRecord,
    ChatRequest,
    ChatResponse,
    CooldownUpdate,
    Decision,
    DeviceCooldown,
    Insight,
    Reflex,
    StateChange,
)

from .deps import get_services
from .ports import Services

router = APIRouter(prefix="/api")


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, s: Services = Depends(get_services)):
    return await s.agent.chat(body)


@router.get("/decisions", response_model=list[Decision])
async def decisions(limit: int = 50, s: Services = Depends(get_services)):
    return await s.actions.list_decisions(limit)


@router.get("/actions", response_model=list[ActionRecord])
async def actions(status: str | None = None, limit: int = 50, s: Services = Depends(get_services)):
    return await s.actions.list_actions(status, limit)


@router.post("/actions/{action_id}/confirm", response_model=ActionRecord)
async def confirm_action(action_id: str, s: Services = Depends(get_services)):
    return await s.actions.confirm(action_id)


@router.post("/actions/{action_id}/reject", response_model=ActionRecord)
async def reject_action(action_id: str, s: Services = Depends(get_services)):
    return await s.actions.reject(action_id)


@router.post("/actions/{action_id}/undo", response_model=ActionRecord)
async def undo_action(action_id: str, s: Services = Depends(get_services)):
    return await s.actions.undo(action_id)


@router.get("/cooldowns", response_model=list[DeviceCooldown])
async def cooldowns(s: Services = Depends(get_services)):
    """Devices the agent is leaving alone after a user override (D9)."""
    return await s.cooldowns.list_cooldowns()


@router.put("/cooldowns/{device_id}", response_model=DeviceCooldown)
async def update_cooldown(device_id: str, body: CooldownUpdate, s: Services = Depends(get_services)):
    """User options: shorter/longer, until unblocked, `minutes: 0` = unblock now;
    `rememberForDevice` applies `minutes` to this device's future overrides (0 = never block)."""
    return await s.cooldowns.update_cooldown(device_id, body)


@router.delete("/cooldowns/{device_id}", status_code=204)
async def clear_cooldown(device_id: str, s: Services = Depends(get_services)):
    await s.cooldowns.clear_cooldown(device_id)
    return Response(status_code=204)


@router.get("/history/changes", response_model=list[StateChange])
async def history_changes(
    deviceId: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    s: Services = Depends(get_services),
):
    return await s.history.list_changes(deviceId, since.isoformat() if since else None, limit)


@router.get("/agent/status")
async def agent_status(s: Services = Depends(get_services)) -> dict[str, Any]:
    """{busy, dirty, lastRunAt, model, beliefState, runsSinceFullSnapshot}"""
    return await s.agent.status()


@router.get("/insights", response_model=list[Insight])
async def insights(s: Services = Depends(get_services)):
    return await s.agent.list_insights()


@router.delete("/insights/{insight_id}", status_code=204)
async def delete_insight(insight_id: str, s: Services = Depends(get_services)):
    await s.agent.delete_insight(insight_id)
    return Response(status_code=204)


@router.get("/reflexes", response_model=list[Reflex])
async def reflexes(status: str | None = None, s: Services = Depends(get_services)):
    return await s.agent.list_reflexes(status)


@router.post("/reflexes/{reflex_id}/confirm", response_model=Reflex)
async def confirm_reflex(reflex_id: str, s: Services = Depends(get_services)):
    return await s.agent.confirm_reflex(reflex_id)


@router.post("/reflexes/{reflex_id}/pause", response_model=Reflex)
async def pause_reflex(reflex_id: str, s: Services = Depends(get_services)):
    return await s.agent.pause_reflex(reflex_id)


@router.post("/reflexes/{reflex_id}/resume", response_model=Reflex)
async def resume_reflex(reflex_id: str, s: Services = Depends(get_services)):
    return await s.agent.resume_reflex(reflex_id)


@router.delete("/reflexes/{reflex_id}", status_code=204)
async def delete_reflex(reflex_id: str, s: Services = Depends(get_services)):
    await s.agent.delete_reflex(reflex_id)
    return Response(status_code=204)

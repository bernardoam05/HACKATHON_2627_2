"""Plugin routes. (Shared contract)"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas import DevicePlugin, PluginProposal

from .deps import get_services
from .ports import Services

router = APIRouter(prefix="/api/plugins")


@router.get("", response_model=list[DevicePlugin])
async def list_plugins(s: Services = Depends(get_services)):
    return await s.plugins.list_plugins()


@router.get("/unconfigured", response_model=list[str])
async def unconfigured(s: Services = Depends(get_services)):
    return await s.plugins.unconfigured_types()


@router.post("/{type}/propose", response_model=PluginProposal)
async def propose(type: str, s: Services = Depends(get_services)):
    return await s.plugins.propose(type)


@router.put("/{type}", response_model=DevicePlugin)
async def confirm(type: str, body: DevicePlugin, s: Services = Depends(get_services)):
    return await s.plugins.confirm_plugin(type, body)

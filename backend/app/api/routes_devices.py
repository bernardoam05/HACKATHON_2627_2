"""Device, room and simulator-admin routes. (Shared contract)"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from app.schemas import (
    CapabilityUpdate,
    Device,
    DeviceAssignment,
    Room,
    RoomCreate,
    SimDeviceCreate,
)

from .deps import get_services
from .ports import Services

router = APIRouter(prefix="/api")


@router.get("/devices", response_model=list[Device])
async def list_devices(s: Services = Depends(get_services)):
    return await s.devices.list_devices()


@router.get("/devices/{device_id}", response_model=Device)
async def get_device(device_id: str, s: Services = Depends(get_services)):
    return await s.devices.get_device(device_id)


@router.patch("/devices/{device_id}", response_model=Device)
async def patch_device(device_id: str, body: DeviceAssignment, s: Services = Depends(get_services)):
    return await s.devices.assign(device_id, body)


@router.post("/devices/{device_id}/capabilities", response_model=Device)
async def set_capability(device_id: str, body: CapabilityUpdate, s: Services = Depends(get_services)):
    return await s.devices.set_capability(device_id, body)


@router.post("/sim/devices", response_model=Device)
async def add_sim_device(body: SimDeviceCreate, s: Services = Depends(get_services)):
    return await s.sim.add_device(body)


@router.delete("/sim/devices/{device_id}", status_code=204)
async def remove_sim_device(device_id: str, s: Services = Depends(get_services)):
    try:
        await s.sim.remove_device(device_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return Response(status_code=204)


@router.get("/sim/templates")
async def sim_templates(s: Services = Depends(get_services)) -> list[dict[str, str]]:
    return await s.sim.list_templates()


@router.get("/rooms", response_model=list[Room])
async def list_rooms(s: Services = Depends(get_services)):
    return await s.rooms.list_rooms()


@router.post("/rooms", response_model=Room)
async def create_room(body: RoomCreate, s: Services = Depends(get_services)):
    return await s.rooms.create_room(body)


@router.delete("/rooms/{room_id}", status_code=204)
async def delete_room(room_id: str, s: Services = Depends(get_services)):
    await s.rooms.delete_room(room_id)
    return Response(status_code=204)

"""Device contracts. Field names keep Altice camelCase on purpose. (Shared contract)"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeviceSource(str, Enum):
    altice = "altice"
    sim = "sim"


class Capability(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    name: str = ""
    value: Any = None
    availableValues: list[Any] | None = None
    min: float | None = None
    max: float | None = None


class Device(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Altice fields
    id: str
    name: str = ""
    type: str
    capabilities: list[Capability] = []
    categories: list[str] = []
    roomName: str | None = None
    lastCommunicationTime: str | None = None
    # Ours
    source: DeviceSource = DeviceSource.altice
    alias: str | None = None
    roomId: str | None = None
    enabled: bool = True

    def capability(self, type: str) -> Capability | None:
        for cap in self.capabilities:
            if cap.type == type:
                return cap
        return None


class CapabilityUpdate(BaseModel):
    """Same as Altice update_device_capabilities.body."""

    type: str
    value: Any = None
    min: float | None = None
    max: float | None = None
    redValue: int | None = None
    greenValue: int | None = None
    blueValue: int | None = None


class Room(BaseModel):
    id: str
    name: str
    isEntrance: bool = False


class RoomCreate(BaseModel):
    name: str
    isEntrance: bool = False


class DeviceAssignment(BaseModel):
    alias: str | None = None
    roomId: str | None = None
    enabled: bool | None = None


class SimDeviceCreate(BaseModel):
    type: str
    name: str
    roomId: str | None = None
    template: str | None = None
    capabilities: list[Capability] | None = None

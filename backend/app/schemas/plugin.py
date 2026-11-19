"""Device-type plugin contracts. (Shared contract)"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class AutonomyLevel(str, Enum):
    auto = "auto"
    auto_notify = "auto_notify"
    suggest = "suggest"


class DevicePurpose(str, Enum):
    comfort = "comfort"
    entertainment = "entertainment"
    energy = "energy"
    security = "security"
    environment = "environment"


class PluginStatus(str, Enum):
    builtin = "builtin"
    proposed = "proposed"
    confirmed = "confirmed"


class ConfigMode(str, Enum):
    basic = "basic"
    advanced = "advanced"


CATEGORY_DEFAULT_AUTONOMY: dict[str, AutonomyLevel] = {
    "security": AutonomyLevel.suggest,
    "energy": AutonomyLevel.auto_notify,
}


class CapabilitySpec(BaseModel):
    type: str
    description: str = ""
    writable: bool = False
    autonomy: AutonomyLevel = AutonomyLevel.suggest


class DevicePlugin(BaseModel):
    type: str
    displayName: str
    purpose: DevicePurpose
    capabilities: list[CapabilitySpec] = []
    starterAutomations: list[str] = []
    status: PluginStatus = PluginStatus.proposed
    configMode: ConfigMode | None = None

    def autonomy_for(self, cap_type: str) -> AutonomyLevel:
        for spec in self.capabilities:
            if spec.type == cap_type:
                return spec.autonomy
        return AutonomyLevel.suggest

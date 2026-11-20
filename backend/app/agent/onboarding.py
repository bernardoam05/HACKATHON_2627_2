"""Unknown-device onboarding (D12).

# Owner: Team A
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from app.plugins.registry import is_security_capability
from app.schemas import (
    AutonomyLevel,
    CapabilitySpec,
    ConfigMode,
    Device,
    DevicePlugin,
    DevicePurpose,
    PluginProposal,
    PluginStatus,
)

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You configure a smart-home device type the house has never seen. From one example device
(type, name, categories and capabilities with current values, ranges and allowed values) propose
the following. The device's own name and type say what it is ("fechadura" = a lock): base
displayName, purpose and ideas on them and on its capabilities, never on other devices.
- displayName: short name in European Portuguese (PT-PT), e.g. "Sensor de movimento".
- purpose: comfort | entertainment | energy | security | environment.
- capabilities: one entry per capability of the device, with a short PT-PT description,
  writable (true only if it is something you can set, like power or brightness; sensors,
  readings, timestamps, battery and signal are read-only) and autonomy:
  auto (harmless, e.g. lights), auto_notify (worth telling the user, e.g. switching a plug off),
  suggest (sensitive: alarm, siren, locks, camera recording, anything you are unsure about).
  Read-only capabilities only inform the house, use auto for them.
- starterAutomations: 1-3 short PT-PT ideas of what the house could do with this device.
- rationale: one or two PT-PT sentences explaining the proposal to the user.
"""


class CapabilityDraft(BaseModel):
    type: str
    description: str = ""
    writable: bool = False
    autonomy: AutonomyLevel = AutonomyLevel.suggest


class PluginDraft(BaseModel):
    displayName: str
    purpose: DevicePurpose
    capabilities: list[CapabilityDraft] = []
    starterAutomations: list[str] = Field(default_factory=list, max_length=5)
    rationale: str = ""


def device_prompt(device: Device) -> str:
    caps = [
        {k: v for k, v in c.model_dump().items() if v not in (None, "", [])}
        for c in device.capabilities
    ]
    return json.dumps(
        {"type": device.type, "name": device.alias or device.name,
         "categories": device.categories, "capabilities": caps},
        ensure_ascii=False, default=str,
    )


def finalize(device: Device, draft: PluginDraft) -> PluginProposal:
    """Never trust the LLM: exactly the device's capabilities, security ones forced to `suggest`,
    status `proposed` (D12: nothing applies until the user confirms)."""
    proposed = {c.type: c for c in draft.capabilities}
    is_security_device = "security" in device.categories or draft.purpose == DevicePurpose.security
    specs: list[CapabilitySpec] = []
    for cap in device.capabilities:
        d = proposed.get(cap.type) or CapabilityDraft(type=cap.type, description=cap.name or cap.type)
        autonomy = d.autonomy
        if is_security_capability(cap.type) or (is_security_device and d.writable):
            autonomy = AutonomyLevel.suggest
        specs.append(CapabilitySpec(type=cap.type, description=d.description,
                                    writable=d.writable, autonomy=autonomy))
    plugin = DevicePlugin(
        type=device.type,
        displayName=draft.displayName.strip() or device.type,
        purpose=draft.purpose,
        capabilities=specs,
        starterAutomations=[s.strip() for s in draft.starterAutomations if s.strip()][:3],
        status=PluginStatus.proposed,
        configMode=ConfigMode.basic,
    )
    return PluginProposal(plugin=plugin, rationale=draft.rationale.strip())


def heuristic_draft(device: Device) -> PluginDraft:
    """Fallback when the LLM is unavailable: conservative and still useful."""
    purpose = (
        DevicePurpose.security if "security" in device.categories
        else DevicePurpose.energy if "energy" in device.categories
        else DevicePurpose.comfort
    )
    caps = [
        CapabilityDraft(
            type=c.type,
            description=c.name or c.type,
            writable=c.type in ("power", "brightness", "temperature", "volume", "color_rgb"),
            autonomy=AutonomyLevel.suggest,
        )
        for c in device.capabilities
    ]
    return PluginDraft(
        displayName=device.name or device.type,
        purpose=purpose,
        capabilities=caps,
        rationale="Proposta automática conservadora: todas as ações pedem confirmação. "
                  "Reveja-a no modo avançado.",
    )


class Onboarding:
    """Basic mode (D12): the agent proposes the plugin; the user accepts or edits (advanced)."""

    def __init__(self, model: Model | str, retries: int = 2) -> None:
        self._agent: Agent[None, PluginDraft] = Agent(
            model, output_type=PluginDraft, instructions=INSTRUCTIONS,
            defer_model_check=True, retries=retries, model_settings={"timeout": 25},
        )

    async def propose(self, device: Device) -> PluginProposal:
        try:
            result = await self._agent.run(device_prompt(device))
            draft = result.output
        except Exception as e:
            log.warning("onboarding LLM failed for %s, using heuristic: %r", device.type, e)
            draft = heuristic_draft(device)
        return finalize(device, draft)


async def propose_plugin(device: Device, model: Any) -> PluginProposal:
    """Return PluginProposal; security-like capabilities must be suggest."""
    return await Onboarding(model).propose(device)

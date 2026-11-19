"""Team A: delta prompts carry enough context to act without tool round-trips."""
from __future__ import annotations

from datetime import UTC, datetime

from app.agent.prompts import build_prompt
from app.schemas import AgentRunInput, Device, StateChange

NOW = datetime(2026, 11, 19, 21, 0, tzinfo=UTC)


def dev(id: str, type: str, room: str, caps: dict) -> Device:
    return Device.model_validate({
        "id": id, "name": id, "type": type, "roomName": room, "source": "sim",
        "capabilities": [{"type": k, "value": v} for k, v in caps.items()],
    })


HOUSE = [
    dev("motion-corridor", "motionSensor", "Corredor", {"motionDetected": True}),
    dev("light-corridor", "bulb", "Corredor", {"power": "off"}),
    dev("motion-kitchen", "motionSensor", "Cozinha", {"motionDetected": False}),
    dev("light-kitchen", "bulb", "Cozinha", {"power": "on"}),
    dev("tv", "tv", "Sala", {"power": "off"}),
]


def test_delta_prompt_describes_changes_and_affected_rooms():
    change = StateChange(id="c1", deviceId="motion-corridor", capability="motionDetected",
                         old=False, new=True, at=NOW)
    prompt = build_prompt(
        AgentRunInput(mode="delta", changesSinceLastRun=[change], now=NOW), devices=HOUSE
    )
    assert '"capability": "motionDetected", "old": false, "new": true, ' \
           '"name": "motion-corridor", "type": "motionSensor", "room": "Corredor"' in prompt
    affected = prompt.split("Devices in the affected rooms (current state):\n", 1)[1]
    assert "light-corridor" in affected
    assert "light-kitchen" not in affected and '"tv"' not in affected  # other rooms stay out


def test_full_prompt_has_no_affected_rooms_section():
    from app.schemas import StateSnapshot

    snap = StateSnapshot(id="s", takenAt=NOW, devices=HOUSE)
    prompt = build_prompt(AgentRunInput(mode="full", snapshot=snap, now=NOW), devices=HOUSE)
    assert "Devices in the affected rooms" not in prompt and "Full snapshot" in prompt


def test_wake_up_prompt_lists_every_device():
    """Door timer: no changes, but the agent must see every light to switch the house off."""
    prompt = build_prompt(
        AgentRunInput(mode="delta", now=NOW), devices=HOUSE, all_devices=True,
        wake_reasons=["30s have passed since the last entrance-door event."],
        presence={"lastDoorEvent": {"deviceId": "door"}, "motionAfterDoorEvent": False},
    )
    listed = prompt.split("All devices (current state):\n", 1)[1]
    assert all(d.id in listed for d in HOUSE)
    assert "Presence facts" in prompt and "Devices in the affected rooms" not in prompt

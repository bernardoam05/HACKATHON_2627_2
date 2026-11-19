"""Contract tests: models round-trip Altice fixtures and key invariants hold."""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import (
    AutonomyLevel,
    CooldownUpdate,
    Device,
    DeviceCooldown,
    DevicePlugin,
    DevicePurpose,
    PresenceFacts,
    StateChangedMsg,
    WsMessage,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_altice_fixture_round_trip():
    path = FIXTURES / "get_home_devices.json"
    if not path.exists():
        pytest.skip("backend/fixtures/get_home_devices.json missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    for raw in data["devices"] if isinstance(data, dict) else data:
        dev = Device.model_validate(raw)
        again = Device.model_validate(json.loads(dev.model_dump_json()))
        assert again.id == dev.id


def test_device_keeps_unknown_altice_fields():
    d = Device.model_validate({"id": "x", "type": "bulb", "somethingNew": 1,
                               "capabilities": [{"type": "power", "value": True, "extra": 2}]})
    assert d.model_dump()["somethingNew"] == 1
    assert d.capability("power").value is True
    assert d.capability("nope") is None


def test_plugin_autonomy_defaults_to_suggest():
    p = DevicePlugin(type="t", displayName="T", purpose=DevicePurpose.comfort)
    assert p.autonomy_for("anything") == AutonomyLevel.suggest


def test_presence_facts_has_no_is_empty():
    assert "isEmpty" not in PresenceFacts.model_fields


def test_ws_discriminated_union():
    msg = TypeAdapter(WsMessage).validate_python({"type": "agent_status", "busy": True, "dirty": False})
    assert msg.type == "agent_status"
    assert StateChangedMsg(changes=[]).type == "state_changed"


def test_cooldown_contract():
    with pytest.raises(ValidationError):
        CooldownUpdate()
    with pytest.raises(ValidationError):
        CooldownUpdate(minutes=5, untilUnblocked=True)
    assert CooldownUpdate(minutes=0).minutes == 0
    cd = DeviceCooldown(deviceId="d", reason="manual_override", startedAt=datetime.now(UTC),
                        defaultMinutes=20)
    assert cd.until is None  # null = until the user unblocks
    msg = TypeAdapter(WsMessage).validate_python(
        {"type": "cooldown_started", "cooldown": json.loads(cd.model_dump_json())})
    assert msg.type == "cooldown_started"
    ended = TypeAdapter(WsMessage).validate_python(
        {"type": "cooldown_ended", "deviceId": "d", "reason": "user"})
    assert ended.type == "cooldown_ended"


def test_now_is_timezone_aware_example():
    assert datetime.now(UTC).tzinfo is not None

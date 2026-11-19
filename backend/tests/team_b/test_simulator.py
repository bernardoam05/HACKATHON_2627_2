"""Simulator behaviour and wire format (simulator/world.py + simulator/server.py)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.schemas import Device

SIM_DIR = Path(__file__).resolve().parents[3] / "simulator"
sys.path.insert(0, str(SIM_DIR))

from world import SimError, World  # noqa: E402

ALTICE_KEYS = {"name", "id", "type", "capabilities", "categories", "roomName", "lastCommunicationTime"}
T0 = 1_800_000_000.0


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def world(clock: Clock) -> World:
    return World(clock=clock, coffee_heat_seconds=120, motion_clear_seconds=30)


def cap(device: dict, cap_type: str):
    return next(c["value"] for c in device["capabilities"] if c["type"] == cap_type)


# --- format ------------------------------------------------------------------
def test_default_home_is_altice_shaped(world: World) -> None:
    devices = world.list_devices()
    assert {d["id"] for d in devices} >= {"sim-tv-sala", "sim-luz-cozinha", "sim-porta-entrada"}
    for d in devices:
        assert set(d) <= ALTICE_KEYS, f"extra keys leak: {set(d) - ALTICE_KEYS}"
        assert all(c["name"] == "" for c in d["capabilities"])
        Device.model_validate(d)
    bulb = next(d for d in devices if d["type"] == "bulb")
    assert "categories" not in bulb  # Altice omits empty categories
    assert cap(bulb, "power") == "off"


def test_details_include_paired_time(world: World) -> None:
    assert "pairedTime" in world.get_device("sim-tv-sala")


def test_templates_use_altice_power_values() -> None:
    for path in (SIM_DIR / "templates").glob("*.json"):
        for c in json.loads(path.read_text(encoding="utf-8"))["capabilities"]:
            assert c["name"] == ""
            if c["type"] == "power":
                assert c["availableValues"] == ["on", "off"] and c["value"] in ("on", "off")


# --- update_device_capabilities (strict, like Altice) ---------------------------
def test_update_power_requires_on_off_strings(world: World) -> None:
    dev = world.update_capabilities({"type": "power", "value": "on"}, device_id="sim-luz-sala")
    assert cap(dev, "power") == "on"
    with pytest.raises(SimError, match="not in"):
        world.update_capabilities({"type": "power", "value": True}, device_id="sim-luz-sala")


def test_update_brightness_range_and_lookup_by_name(world: World) -> None:
    dev = world.update_capabilities({"type": "brightness", "value": 30}, device_name="luz da sala")
    assert dev["id"] == "sim-luz-sala" and cap(dev, "brightness") == 30
    with pytest.raises(SimError, match="out of range"):
        world.update_capabilities({"type": "brightness", "value": 0}, device_id="sim-luz-sala")


def test_sensor_readings_are_not_writable(world: World) -> None:
    with pytest.raises(SimError, match="not writable"):
        world.update_capabilities({"type": "motionDetected", "value": True}, device_id="sim-movimento-sala")
    with pytest.raises(SimError, match="not writable"):
        world.update_capabilities({"type": "instantPower", "value": 5}, device_id="sim-cafe-cozinha")


def test_update_unknown_device_or_missing_capability(world: World) -> None:
    with pytest.raises(SimError, match="unknown device"):
        world.update_capabilities({"type": "power", "value": "on"}, device_id="nope")
    with pytest.raises(SimError, match="no capability"):
        world.update_capabilities({"type": "brightness", "value": 10}, device_id="sim-tv-sala")


def test_color_rgb_switches_work_mode(world: World) -> None:
    dev = world.update_capabilities(
        {"type": "color_rgb", "redValue": 255, "greenValue": 120, "blueValue": 0}, device_id="sim-luz-sala"
    )
    assert cap(dev, "workMode") == "colour"
    assert cap(dev, "color_rgb") == {"redValue": 255, "greenValue": 120, "blueValue": 0}


# --- behaviours ----------------------------------------------------------------
def test_tv_power_draw_follows_power(world: World) -> None:
    dev = world.set_state("sim-tv-sala", "power", True)
    assert cap(dev, "power") == "on" and cap(dev, "instantPower") == 85
    dev = world.set_state("sim-tv-sala", "power", "off")
    assert cap(dev, "instantPower") == 0


def test_coffee_plug_heats_then_idles(world: World, clock: Clock) -> None:
    dev = world.update_capabilities({"type": "power", "value": "on"}, device_id="sim-cafe-cozinha")
    assert cap(dev, "instantPower") == 1200
    clock.t += 60
    world.update_capabilities({"type": "power", "value": "on"}, device_id="sim-cafe-cozinha")  # no restart
    clock.t += 61
    assert cap(world.get_device("sim-cafe-cozinha"), "instantPower") == 2
    dev = world.update_capabilities({"type": "power", "value": "off"}, device_id="sim-cafe-cozinha")
    assert cap(dev, "instantPower") == 0


def test_motion_sets_timestamp_and_clears(world: World, clock: Clock) -> None:
    dev = world.set_state("sim-movimento-cozinha", "motionDetected", True)
    assert cap(dev, "lastMotionAt") == int(T0 * 1000)
    clock.t += 20
    world.set_state("sim-movimento-cozinha", "motionDetected", "true")  # re-detection extends
    clock.t += 20
    assert cap(world.get_device("sim-movimento-cozinha"), "motionDetected") is True
    clock.t += 11
    dev = world.get_device("sim-movimento-cozinha")
    assert cap(dev, "motionDetected") is False
    assert cap(dev, "lastMotionAt") == int((T0 + 20) * 1000)


def test_contact_sensor_friendly_values(world: World) -> None:
    assert cap(world.set_state("sim-porta-entrada", "sensorState", "open"), "sensorState") == "alarm"
    assert cap(world.set_state("sim-porta-entrada", "sensorState", "fechada"), "sensorState") == "normal"
    with pytest.raises(SimError):
        world.set_state("sim-porta-entrada", "sensorState", "maybe")


def test_change_updates_last_communication_time(world: World, clock: Clock) -> None:
    clock.t += 5
    dev = world.set_state("sim-tv-sala", "volume", 30)
    assert dev["lastCommunicationTime"] == int((T0 + 5) * 1000)


# --- admin tools -----------------------------------------------------------------
def test_add_and_remove_device(world: World) -> None:
    dev = world.add_device("motionSensor", "Movimento do quarto", "Quarto",
                           capabilities=[{"type": "batteryLevel", "value": 90, "min": 0, "max": 100}])
    assert dev["id"].startswith("sim-motionSensor-") and dev["roomName"] == "Quarto"
    assert cap(dev, "batteryLevel") == 90
    assert world.remove_device(dev["id"]) == {"deviceId": dev["id"], "removed": True}
    with pytest.raises(SimError, match="unknown device"):
        world.remove_device(dev["id"])
    with pytest.raises(SimError, match="unknown template"):
        world.add_device("toaster", "Torradeira")


def test_list_templates(world: World) -> None:
    ids = {t["id"] for t in world.list_templates()}
    assert ids >= {"tv", "coffee_plug", "bulb", "contactSensor", "motionSensor"}


def test_load_scenario_resets_and_applies_state(world: World) -> None:
    world.add_device("bulb", "Extra")
    out = world.load_scenario("s2")
    devices = {d["id"]: d for d in out["devices"]}
    assert not any(d["name"] == "Extra" for d in devices.values())
    assert cap(devices["sim-luz-cozinha"], "power") == "on"
    assert cap(devices["sim-luz-cozinha"], "brightness") == 80
    assert cap(devices["sim-movimento-cozinha"], "motionDetected") is True


@pytest.mark.parametrize("name", ["../secrets", "nope", ""])
def test_load_scenario_rejects_bad_names(world: World, name: str) -> None:
    with pytest.raises(SimError):
        world.load_scenario(name)


# --- MCP wire format (what Team A's client sees) ---------------------------------
@pytest.fixture
def server():
    pytest.importorskip("mcp.server.mcpserver")
    spec = importlib.util.spec_from_file_location("sim_server", SIM_DIR / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_mcp_tools_return_single_json_object(server) -> None:
    from mcp.client import Client

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_home_devices", {})
        assert not result.is_error and len(result.content) == 1
        payload = json.loads(result.content[0].text)
        assert payload == result.structured_content
        for d in payload["devices"]:
            Device.model_validate(d)

        result = await client.call_tool("sim_list_templates", {})
        assert len(result.content) == 1 and "templates" in result.structured_content

        result = await client.call_tool(
            "update_device_capabilities",
            {"deviceId": "sim-luz-sala", "body": {"type": "power", "value": "on"}},
        )
        assert not result.is_error and result.structured_content["id"] == "sim-luz-sala"


async def test_mcp_refusal_keeps_reason(server) -> None:
    from mcp.client import Client

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "update_device_capabilities",
            {"deviceId": "sim-luz-sala", "body": {"type": "power", "value": True}},
        )
        assert result.is_error
        assert "not in" in result.content[0].text

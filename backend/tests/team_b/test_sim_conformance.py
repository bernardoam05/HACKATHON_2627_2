"""Simulator templates validate as Device and match the Altice data format."""
import json
from pathlib import Path

import pytest

from app.schemas import Device

TEMPLATES = sorted((Path(__file__).resolve().parents[3] / "simulator" / "templates").glob("*.json"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_is_valid_device(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    dev = Device.model_validate({"id": f"sim-{path.stem}", **raw})
    assert dev.capabilities, "template must expose capabilities"


def test_expected_templates_present():
    assert {p.stem for p in TEMPLATES} >= {"tv", "coffee_plug", "bulb", "contactSensor", "motionSensor"}


def test_server_registers_altice_tool_names():
    pytest.importorskip("mcp")
    src = (Path(__file__).resolve().parents[3] / "simulator" / "server.py").read_text(encoding="utf-8")
    for name in ("get_home_devices", "get_device_details", "update_device_capabilities", "get_home_rooms",
                 "sim_list_templates", "sim_add_device", "sim_remove_device", "sim_set_state",
                 "sim_load_scenario"):
        assert f"def {name}(" in src

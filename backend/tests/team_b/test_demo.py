"""Demo assets stay runnable: scenarios replay on the simulator, the seed imports and shows the routines."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from app.schemas import Device

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = sorted((ROOT / "demo" / "scenarios").glob("s*.yaml"))
sys.path.insert(0, str(ROOT / "simulator"))

from world import World  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_history = _load("demo_seed_history", ROOT / "demo" / "seed_history.py")


def cap(device: dict, cap_type: str):
    return next((c["value"] for c in device["capabilities"] if c["type"] == cap_type), "<missing>")


def test_all_five_scenarios_exist() -> None:
    assert [p.stem for p in SCENARIOS] == ["s1", "s2", "s3", "s4", "s5"]


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_replays_on_the_simulator(path: Path) -> None:
    """Load the start state, apply every step like run_scenario.py, and check expectations point at
    real devices/capabilities (the agent's actions themselves need the live backend)."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    world = World(motion_clear_seconds=0)
    world.load_scenario(path.stem)
    assert spec.get("steps"), "a demo scenario needs at least one step"

    for step in spec["steps"]:
        assert set(step) <= {"say", "wait", "set", "add"}, f"unknown step keys {set(step)}"
        if "set" in step:
            s = step["set"]
            world.set_state(s["device"], s["capability"], s["value"])
        elif "add" in step:
            a = step["add"]
            Device.model_validate(world.add_device(a["template"], a["name"], a.get("roomName"), a.get("capabilities")))

    devices = {d["id"]: d for d in world.list_devices()}
    for e in spec.get("expect") or []:
        assert float(e.get("within", 30)) > 0
        if "device" in e:
            assert e["device"] in devices, f"expect refers to unknown device {e['device']}"
            assert cap(devices[e["device"]], e["capability"]) != "<missing>"
            assert e.get("op", "=") in {"=", "!=", "<", ">"}
        else:
            assert "pending" in e or "unconfigured" in e, f"unknown expectation {e}"


def test_s5_type_is_really_new() -> None:
    world = World()
    known = {d["type"] for d in world.list_devices()} | {t["type"] for t in world.list_templates()}
    spec = yaml.safe_load((ROOT / "demo" / "scenarios" / "s5.yaml").read_text(encoding="utf-8"))
    assert spec["expect"][0]["unconfigured"] not in known


def test_seed_is_deterministic_and_has_the_routines() -> None:
    end = date(2026, 11, 19)
    rows = seed_history.generate(14, end, UTC, 42)
    assert rows == seed_history.generate(14, end, UTC, 42)
    assert len({r["id"] for r in rows}) == len(rows)
    weekday_coffee = [datetime.fromisoformat(r["at"]) for r in rows
                      if r["deviceId"] == "sim-cafe-cozinha" and r["capability"] == "power" and r["new"] == "on"
                      and datetime.fromisoformat(r["at"]).weekday() < 5]
    assert len(weekday_coffee) == 10
    assert all(timedelta(hours=7, minutes=10) <= t - t.replace(hour=0, minute=0, second=0) <= timedelta(hours=7, minutes=50)
               for t in weekday_coffee)


def test_seed_file_imports_and_routine_stats_find_the_coffee() -> None:
    from app.agent.tools import routine_stats
    from app.storage.import_seed import parse_lines

    path = ROOT / "demo" / "seed" / "history.jsonl"
    changes, skipped = parse_lines(path.read_text(encoding="utf-8").splitlines())
    assert skipped == 0 and len(changes) > 100
    latest = max(c.at for c in changes)
    coffee = [c for c in changes if c.deviceId == "sim-cafe-cozinha" and c.capability == "power"]
    stats = routine_stats(coffee, "sim-cafe-cozinha", "power", 14, 20, latest.astimezone(UTC) + timedelta(hours=1))
    assert stats["weekdays"]["activeDays"] >= 9
    assert "07:20" <= stats["weekdays"]["medianFirstOn"] <= "07:40"

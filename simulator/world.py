"""In-memory simulated house used by the simulator MCP server (PLAN 5.7).

# Owner: Team B
No MCP dependency on purpose, so backend tests can import and drive it directly.

Payloads mirror the Altice MCP (see backend/fixtures/): capability `name` is "",
power is "on"/"off", contact sensors report `sensorState` normal/alarm, timestamps
are epoch millis. Timed behaviours (coffee heating, motion auto-clear) are evaluated
lazily on every call, so the poller sees them within one poll interval.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("simulator")

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
HOME_FILE = HERE / "home.json"
SCENARIOS_DIR = HERE.parent / "demo" / "scenarios"

# Capabilities update_device_capabilities may write. The first four are the Altice
# write tool's body types; volume is sim-only (the TV has no Altice counterpart).
# Everything else is a sensor reading: only sim_set_state (a physical event) changes it.
WRITABLE = frozenset({"power", "brightness", "temperature", "color_rgb", "volume"})

TV_ON_WATTS = 85
COFFEE_HEAT_WATTS = 1200
COFFEE_IDLE_WATTS = 2

_SCENARIO_NAME = re.compile(r"[A-Za-z0-9_-]+")
_TYPE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_ON = {"on", "true", "1", "yes", "ligado", "ligada"}
_OFF = {"off", "false", "0", "no", "desligado", "desligada"}
_OPEN = {"open", "opened", "true", "1", "aberto", "aberta", "alarm"}
_CLOSED = {"closed", "false", "0", "fechado", "fechada", "normal"}


class SimError(Exception):
    """A request the simulator refuses (unknown device, read-only capability, bad value)."""


@dataclass
class SimDevice:
    id: str
    template: str
    name: str
    type: str
    categories: list[str]
    roomName: str | None
    capabilities: list[dict[str, Any]]
    pairedTime: int
    lastCommunicationTime: int
    timers: dict[str, float] = field(default_factory=dict)  # behaviour deadlines (epoch s)

    def cap(self, cap_type: str) -> dict[str, Any] | None:
        return next((c for c in self.capabilities if c["type"] == cap_type), None)

    def to_altice(self, *, details: bool = False) -> dict[str, Any]:
        """Altice shape: no `source`/`template`/timers; `categories` omitted when empty."""
        out: dict[str, Any] = {
            "name": self.name,
            "id": self.id,
            "type": self.type,
            "capabilities": copy.deepcopy(self.capabilities),
        }
        if self.categories:
            out["categories"] = list(self.categories)
        out["roomName"] = self.roomName
        out["lastCommunicationTime"] = self.lastCommunicationTime
        if details:
            out["pairedTime"] = self.pairedTime
        return out


class World:
    """The simulated house. All public methods are thread-safe and advance timers first."""

    def __init__(
        self,
        *,
        templates_dir: Path = TEMPLATES_DIR,
        home_file: Path = HOME_FILE,
        scenarios_dir: Path = SCENARIOS_DIR,
        clock: Callable[[], float] = time.time,
        coffee_heat_seconds: float = 120,
        motion_clear_seconds: float = 30,
    ) -> None:
        self._clock = clock
        self._scenarios_dir = scenarios_dir
        self.coffee_heat_seconds = coffee_heat_seconds
        self.motion_clear_seconds = motion_clear_seconds
        self._lock = threading.RLock()
        self._templates: dict[str, dict[str, Any]] = {
            p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(templates_dir.glob("*.json"))
        }
        self._home: list[dict[str, Any]] = json.loads(home_file.read_text(encoding="utf-8"))["devices"]
        self._devices: dict[str, SimDevice] = {}
        self.reset()

    # --- Altice-compatible -------------------------------------------------
    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            self._tick()
            return [d.to_altice() for d in self._devices.values()]

    def get_device(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            self._tick()
            return self._find(device_id).to_altice(details=True)

    def update_capabilities(
        self, body: dict[str, Any], device_id: str | None = None, device_name: str | None = None
    ) -> dict[str, Any]:
        """Strict, like the real house: only writable capabilities, exact values, in range."""
        with self._lock:
            self._tick()
            dev = self._resolve(device_id, device_name)
            cap_type = body.get("type")
            if cap_type not in WRITABLE:
                raise SimError(
                    f"capability {cap_type!r} is not writable; writable: {sorted(WRITABLE)} "
                    "(sensor values change only through sim_set_state)"
                )
            if cap_type == "color_rgb":
                self._write_color(dev, body)
            else:
                cap = dev.cap(cap_type)
                if cap is None:
                    raise SimError(f"device {dev.id} ({dev.type}) has no capability {cap_type!r}")
                if "value" not in body:
                    raise SimError(f"body for {cap_type!r} needs a 'value'")
                self._set(dev, cap_type, _strict_value(cap, body["value"]), source="command")
            return dev.to_altice()

    # --- Simulator-only ------------------------------------------------------
    def list_templates(self) -> list[dict[str, str]]:
        return [{"id": k, "type": t["type"], "name": t["name"]} for k, t in self._templates.items()]

    def add_device(
        self,
        template: str,
        name: str | None = None,
        room_name: str | None = None,
        capabilities: list[dict[str, Any]] | None = None,
        device_id: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._tick()
            dev = self._create(template, name, room_name, capabilities, device_id)
            self._devices[dev.id] = dev
            log.info("added %s (%s) in %s", dev.id, template, room_name)
            for cap_type, value in (state or {}).items():
                self._physical(dev, cap_type, value)
            return dev.to_altice()

    def remove_device(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            self._find(device_id)
            del self._devices[device_id]
            log.info("removed %s", device_id)
            return {"deviceId": device_id, "removed": True}

    def set_state(self, device_id: str, capability: str, value: Any) -> dict[str, Any]:
        """A physical event (remote pressed, someone walks by, door opened). Lenient values."""
        with self._lock:
            self._tick()
            dev = self._find(device_id)
            self._physical(dev, capability, value)
            return dev.to_altice()

    def reset(self, specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Replace every device with `specs` (default: the home in home.json)."""
        with self._lock:
            self._devices = {}
            for spec in specs if specs else self._home:
                self.add_device(
                    template=spec["template"],
                    name=spec.get("name"),
                    room_name=spec.get("roomName"),
                    capabilities=spec.get("capabilities"),
                    device_id=spec.get("id"),
                    state=spec.get("state"),
                )
            return [d.to_altice() for d in self._devices.values()]

    def load_scenario(self, name: str) -> dict[str, Any]:
        """Reset to the scenario's devices (or the default home) and apply its start `state`."""
        if not _SCENARIO_NAME.fullmatch(name):
            raise SimError(f"invalid scenario name {name!r}")
        path = self._scenarios_dir / f"{name}.yaml"
        if not path.is_file():
            known = sorted(p.stem for p in self._scenarios_dir.glob("*.yaml"))
            raise SimError(f"unknown scenario {name!r}; known: {known}")
        import yaml  # local import: only scenario loading needs PyYAML

        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        with self._lock:
            self.reset(spec.get("devices") or None)
            for device_id, caps in (spec.get("state") or {}).items():
                dev = self._find(device_id)
                for cap_type, value in caps.items():
                    self._physical(dev, cap_type, value)
            log.info("loaded scenario %s", name)
            return {"scenario": name, "devices": [d.to_altice() for d in self._devices.values()]}

    # --- internals -----------------------------------------------------------
    def _now(self) -> float:
        return self._clock()

    def _now_ms(self) -> int:
        return int(self._now() * 1000)

    def _find(self, device_id: str) -> SimDevice:
        dev = self._devices.get(device_id)
        if dev is None:
            raise SimError(f"unknown device {device_id!r}")
        return dev

    def _resolve(self, device_id: str | None, device_name: str | None) -> SimDevice:
        if device_id:
            return self._find(device_id)
        if not device_name:
            raise SimError("deviceId or deviceName is required")
        matches = [d for d in self._devices.values() if d.name.casefold() == device_name.casefold()]
        if len(matches) != 1:
            raise SimError(f"deviceName {device_name!r} matches {len(matches)} devices")
        return matches[0]

    def _create(
        self,
        template: str,
        name: str | None,
        room_name: str | None,
        capabilities: list[dict[str, Any]] | None,
        device_id: str | None,
    ) -> SimDevice:
        tpl = self._templates.get(template) or self._custom_type(template, capabilities)
        device_id = device_id or f"sim-{template}-{uuid.uuid4().hex[:8]}"
        if device_id in self._devices:
            raise SimError(f"device {device_id!r} already exists")
        caps = copy.deepcopy(tpl["capabilities"])
        for override in capabilities or []:
            _apply_override(caps, override)
        now_ms = self._now_ms()
        return SimDevice(
            id=device_id,
            template=template,
            name=name or tpl["name"],
            type=tpl["type"],
            categories=list(tpl.get("categories", [])),
            roomName=room_name,
            capabilities=caps,
            pairedTime=now_ms,
            lastCommunicationTime=now_ms,
        )

    def _custom_type(self, type_id: str, capabilities: list[dict[str, Any]] | None) -> dict[str, Any]:
        """A type with no template (S5: the user invents one): the given capabilities define it.

        The backend sends `template or type`, so `type_id` is the new type. It has no behaviour
        and no categories, so the backend treats it as unknown (suggest until configured).
        """
        if not capabilities:
            raise SimError(
                f"unknown template {type_id!r}; known: {sorted(self._templates)}. "
                "A new type needs at least one capability."
            )
        if not _TYPE_ID.fullmatch(type_id):
            raise SimError(f"invalid device type {type_id!r}: use letters and digits, e.g. smartLock")
        return {"type": type_id, "name": type_id, "categories": [], "capabilities": []}

    def _physical(self, dev: SimDevice, cap_type: str, value: Any) -> None:
        cap = dev.cap(cap_type)
        if cap is None:
            known = [c["type"] for c in dev.capabilities]
            raise SimError(f"device {dev.id} has no capability {cap_type!r}; has: {known}")
        self._set(dev, cap_type, _lenient_value(cap, value), source="physical")

    def _set(self, dev: SimDevice, cap_type: str, value: Any, *, source: str) -> None:
        cap = dev.cap(cap_type)
        assert cap is not None
        old = cap["value"]
        cap["value"] = value
        dev.lastCommunicationTime = self._now_ms()
        if old != value:
            log.info("%s %s: %r -> %r (%s)", dev.id, cap_type, old, value, source)
        self._on_change(dev, cap_type, old, value)

    def _write_color(self, dev: SimDevice, body: dict[str, Any]) -> None:
        if dev.cap("workMode") is None:
            raise SimError(f"device {dev.id} ({dev.type}) does not support color_rgb")
        rgb = {}
        for key in ("redValue", "greenValue", "blueValue"):
            v = body.get(key)
            if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 255:
                raise SimError(f"color_rgb needs {key} as a number 0..255, got {v!r}")
            rgb[key] = int(v)
        if dev.cap("color_rgb") is None:
            dev.capabilities.append({"name": "", "type": "color_rgb", "value": None})
        self._set(dev, "color_rgb", rgb, source="command")
        self._set(dev, "workMode", "colour", source="command")

    # --- behaviours ------------------------------------------------------------
    def _on_change(self, dev: SimDevice, cap_type: str, old: Any, new: Any) -> None:
        if cap_type == "power" and dev.template == "tv":
            self._set(dev, "instantPower", TV_ON_WATTS if new == "on" else 0, source="behaviour")
        elif cap_type == "power" and dev.template == "coffee_plug" and old != new:
            if new == "on":
                dev.timers["heat"] = self._now() + self.coffee_heat_seconds
                self._set(dev, "instantPower", COFFEE_HEAT_WATTS, source="behaviour")
            else:
                dev.timers.pop("heat", None)
                self._set(dev, "instantPower", 0, source="behaviour")
        elif cap_type == "motionDetected" and new is True:
            # Re-detection refreshes lastMotionAt and the clear timer, like a real PIR.
            self._set(dev, "lastMotionAt", self._now_ms(), source="behaviour")
            if self.motion_clear_seconds > 0:
                dev.timers["motion"] = self._now() + self.motion_clear_seconds
        elif cap_type == "motionDetected" and new is False:
            dev.timers.pop("motion", None)

    def _tick(self) -> None:
        now = self._now()
        for dev in list(self._devices.values()):
            if dev.timers.get("heat", now + 1) <= now:
                del dev.timers["heat"]
                self._set(dev, "instantPower", COFFEE_IDLE_WATTS, source="behaviour")
            if dev.timers.get("motion", now + 1) <= now:
                del dev.timers["motion"]
                self._set(dev, "motionDetected", False, source="behaviour")


# --- value validation ----------------------------------------------------------
_OVERRIDE_META = ("availableValues", "min", "max")


def _apply_override(caps: list[dict[str, Any]], override: dict[str, Any]) -> None:
    """Merge one sim_add_device capability override, keeping the Altice format valid.

    Metadata (availableValues/min/max) is applied first, then the value (given or current)
    is validated against it with the same friendly spellings as sim_set_state, so
    {"type": "power", "value": true} is stored as "on" and out-of-range values are refused.
    """
    cap_type = override.get("type")
    if not isinstance(cap_type, str) or not cap_type:
        raise SimError(f"capability override needs a 'type': {override!r}")
    unknown = set(override) - {"type", "name", "value", *_OVERRIDE_META}
    if unknown:
        raise SimError(f"capability override {cap_type!r} has unknown fields {sorted(unknown)}")
    cap = next((c for c in caps if c["type"] == cap_type), None)
    if cap is None:
        cap = {"name": "", "type": cap_type, "value": None}
        caps.append(cap)
    for key in _OVERRIDE_META:
        if key in override:
            cap[key] = copy.deepcopy(override[key])
    value = override.get("value", cap["value"])
    if value is not None:
        cap["value"] = _lenient_value(cap, value)



def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _check_range(cap: dict[str, Any], v: float) -> int | float:
    lo, hi = cap.get("min"), cap.get("max")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SimError(f"{cap['type']}: {v} out of range [{lo}, {hi}]")
    return int(v) if float(v).is_integer() else v


def _is_numeric_cap(cap: dict[str, Any]) -> bool:
    return cap.get("min") is not None or cap.get("max") is not None or _is_number(cap.get("value"))


def _strict_value(cap: dict[str, Any], value: Any) -> Any:
    allowed = cap.get("availableValues")
    if allowed is not None:
        if value not in allowed:
            raise SimError(f"{cap['type']}: {value!r} not in {allowed}")
        return value
    if _is_numeric_cap(cap):
        if not _is_number(value):
            raise SimError(f"{cap['type']}: expected a number, got {value!r}")
        return _check_range(cap, value)
    return value


def _lenient_value(cap: dict[str, Any], value: Any) -> Any:
    """Accept friendly spellings for physical events: True/"open"/"aberta"/"ligado"..."""
    allowed = cap.get("availableValues")
    text = str(value).strip().casefold()
    if allowed is not None:
        options = set(allowed)
        if options == {"on", "off"}:
            value = "on" if text in _ON else "off" if text in _OFF else value
        elif options == {"normal", "alarm"}:
            value = "alarm" if text in _OPEN else "normal" if text in _CLOSED else value
        return _strict_value(cap, value)
    if isinstance(cap.get("value"), bool):
        if text in _ON | {"detected", "motion"}:
            return True
        if text in _OFF | {"clear", "none"}:
            return False
        raise SimError(f"{cap['type']}: expected a boolean, got {value!r}")
    if _is_numeric_cap(cap):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise SimError(f"{cap['type']}: expected a number, got {value!r}") from exc
        return _check_range(cap, number)
    return value

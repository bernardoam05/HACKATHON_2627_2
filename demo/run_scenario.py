"""Run a demo scenario: python demo/run_scenario.py s1

# Owner: Team B
Loads demo/scenarios/<name>.yaml, calls sim_load_scenario, then replays the steps via sim_set_state.
Never calls Altice write tools: it only talks to the simulator MCP (SIM_MCP_URL) and to the backend
REST API (reads, plus rooms / device assignment / cooldowns for the scenario setup).

Run from the simulator environment (it has mcp + pyyaml):
    cd simulator && uv run python ../demo/run_scenario.py s1 [--no-check] [--speed 2]

Scenarios that need the backend's startup run (S3: routines) must load the state BEFORE the
backend starts, otherwise the reset undoes what the agent just did (read as a user override):
    uv run python ../demo/run_scenario.py s3 --load-only     # 1. start state
    (start the backend with the scenario's env)              # 2. startup run sees that state
    uv run python ../demo/run_scenario.py s3 --skip-load     # 3. play + check

Scenario file (demo/scenarios/sN.yaml):
    devices / state      start state, applied by sim_load_scenario (see simulator/world.py);
                         with a running backend, cooldowns the reset causes on these devices are
                         lifted right after loading
    backend_env          env the backend should be started with (printed as a reminder)
    backend_setup:       done through the backend REST API before the steps
      rooms: [{name, isEntrance?, devices: [ids]}]   rooms created if missing, devices assigned
    steps:               in order; each step may have `say`, `wait` (seconds, before the action)
                         and one action:
      - set: {device, capability, value}            physical event (sim_set_state)
      - add: {template, name, roomName?, capabilities?}   new simulated device (sim_add_device)
    expect:              checked after the steps, polled every second until `within` seconds:
      - {device, capability, op: "=" | "!=" | "<" | ">", value}   simulator state
      - {pending: {capability, device?}}            a suggestion waiting for the user (backend)
      - {unconfigured: <type>}                      a type waiting for configuration (backend)
      optional: true  -> reported, but does not fail the run
Exit code: 0 when every required expectation passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from mcp.client import Client

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
SIM_URL = os.getenv("SIM_MCP_URL", "http://localhost:8100/mcp")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# The only MCP tools this script may call: all simulator-side (CLAUDE.md: no Altice writes).
ALLOWED_TOOLS = frozenset({"sim_load_scenario", "sim_set_state", "sim_add_device", "get_device_details"})


class ScenarioError(Exception):
    pass


class Sim:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if tool not in ALLOWED_TOOLS:
            raise ScenarioError(f"tool {tool!r} is not allowed in demo scripts")
        result = await self._client.call_tool(tool, args)
        if result.is_error:
            text = result.content[0].text if result.content else "unknown error"
            raise ScenarioError(f"{tool}: {text}")
        return result.structured_content


def backend_get(path: str) -> Any:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}{path}", timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def backend_send(method: str, path: str, body: Any = None) -> Any:
    """Backend REST only (rooms, device assignment, cooldowns): never an Altice write tool."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{BACKEND_URL}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else True
    except urllib.error.HTTPError as e:
        raise ScenarioError(f"backend {method} {path} -> {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ScenarioError(f"backend not reachable at {BACKEND_URL}: {e}") from e


def setup_backend(spec: dict[str, Any]) -> None:
    """`backend_setup.rooms`: [{name, isEntrance, devices: [ids]}] -> rooms (ours, D11) created if
    missing and devices assigned to them, e.g. the entrance room holding the front door (D14)."""
    rooms = (spec.get("backend_setup") or {}).get("rooms") or []
    if not rooms:
        return
    existing = {r["name"]: r for r in backend_get("/api/rooms") or []}
    for room in rooms:
        r = existing.get(room["name"]) or backend_send(
            "POST", "/api/rooms", {"name": room["name"], "isEntrance": bool(room.get("isEntrance"))})
        for device_id in room.get("devices") or []:
            backend_send("PATCH", f"/api/devices/{device_id}", {"roomId": r["id"]})
        say(f"divisão '{room['name']}'" + (" (entrada)" if room.get("isEntrance") else "")
            + f" com {len(room.get('devices') or [])} dispositivo(s)")


async def clear_reset_cooldowns(spec: dict[str, Any]) -> None:
    """Loading the start state is applied as physical events; if the agent had acted on those
    devices just before, the backend reads the reset as a user override (D9) and blocks them.
    Wait one poll, then lift those blocks so the scenario starts clean."""
    devices = list((spec.get("state") or {}).keys())
    if not devices or backend_get("/api/agent/status") is None:
        return
    await asyncio.sleep(3)
    for device_id in devices:
        try:
            backend_send("DELETE", f"/api/cooldowns/{device_id}")
        except ScenarioError:
            pass  # unknown device or older backend without cooldowns: nothing to lift


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "=":
        return actual == expected
    if op == "!=":
        return actual != expected
    if not isinstance(actual, int | float) or isinstance(actual, bool):
        return False
    return actual < expected if op == "<" else actual > expected


def describe(e: dict[str, Any]) -> str:
    if "say" in e:
        return str(e["say"])
    if "pending" in e:
        return f"sugestão pendente: {e['pending']}"
    if "unconfigured" in e:
        return f"tipo por configurar: {e['unconfigured']}"
    return f"{e['device']}.{e['capability']} {e.get('op', '=')} {e['value']!r}"


async def check(sim: Sim, e: dict[str, Any]) -> tuple[bool, str]:
    """One poll of one expectation -> (passed, what was seen)."""
    if "pending" in e:
        want = e["pending"]
        actions = backend_get("/api/actions?status=pending_confirmation&limit=50") or []
        hit = [a for a in actions if a["update"]["type"] == want["capability"]
               and want.get("device") in (None, a["deviceId"])]
        return bool(hit), f"{len(actions)} pendentes"
    if "unconfigured" in e:
        types = backend_get("/api/plugins/unconfigured") or []
        return e["unconfigured"] in types, f"por configurar: {types}"
    dev = await sim.call("get_device_details", {"deviceId": e["device"]})
    value = next((c["value"] for c in dev["capabilities"] if c["type"] == e["capability"]), None)
    return compare(value, e.get("op", "="), e["value"]), f"valor atual {value!r}"


async def run(name: str, do_check: bool, speed: float, load: str = "auto") -> bool:
    """`load`: "auto" (load the start state, lift reset cooldowns, play), "only" (load the start
    state and stop: start the backend afterwards), "skip" (state already loaded: just play)."""
    path = SCENARIOS / f"{name}.yaml"
    if not path.is_file():
        raise ScenarioError(f"no scenario {path}")
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    print(f"\n== {name}: {spec.get('description', '')}")
    if spec.get("now_override") or spec.get("backend_env"):
        env = dict(spec.get("backend_env") or {})
        if spec.get("now_override"):
            env["NOW_OVERRIDE"] = spec["now_override"]
        print("  backend deve estar a correr com: " + " ".join(f"{k}={v}" for k, v in env.items()))

    async with Client(SIM_URL) as client:
        sim = Sim(client)
        if load != "skip":
            await sim.call("sim_load_scenario", {"name": name})
            say("estado inicial carregado no simulador")
        if load == "only":
            print("== ESTADO CARREGADO: arranca agora o backend e depois corre com --skip-load")
            return True
        setup_backend(spec)
        if load == "auto":
            await clear_reset_cooldowns(spec)

        for step in spec.get("steps") or []:
            if step.get("wait"):
                await asyncio.sleep(float(step["wait"]) / speed)
            if step.get("say"):
                say(f"> {step['say']}")
            if "set" in step:
                s = step["set"]
                await sim.call("sim_set_state", {"deviceId": s["device"], "capability": s["capability"],
                                                 "value": s["value"]})
            elif "add" in step:
                a = step["add"]
                await sim.call("sim_add_device", {k: a[k] for k in ("template", "name", "roomName", "capabilities")
                                                  if k in a})

        expectations = spec.get("expect") or []
        if not do_check or not expectations:
            print("== CONCLUÍDO (sem verificação)")
            return True

        print("  a verificar o que a casa faz…")
        start = time.monotonic()
        pending = list(enumerate(expectations))
        results: dict[int, tuple[bool, str]] = {}
        while pending:
            still = []
            for i, e in pending:
                ok, seen = await check(sim, e)
                timed_out = time.monotonic() - start > float(e.get("within", 30)) / speed
                if ok or timed_out:
                    results[i] = (ok, seen)
                    mark = "OK  " if ok else ("--  " if e.get("optional") else "FALHOU")
                    say(f"[{mark}] {describe(e)}" + ("" if ok else f" ({seen})"))
                else:
                    still.append((i, e))
            pending = still
            if pending:
                await asyncio.sleep(1)

    return all(ok or expectations[i].get("optional") for i, (ok, _) in results.items())


def _first_leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def main() -> None:
    p = argparse.ArgumentParser(description="Run a demo scenario against the simulator.")
    p.add_argument("name", nargs="?", default="s1")
    p.add_argument("--no-check", action="store_true", help="only play the steps")
    p.add_argument("--speed", type=float, default=1.0, help="divide waits and timeouts by this")
    order = p.add_mutually_exclusive_group()
    order.add_argument("--load-only", action="store_true",
                       help="load the start state and stop (then start the backend)")
    order.add_argument("--skip-load", action="store_true",
                       help="the start state is already loaded: only play the steps")
    a = p.parse_args()
    load = "only" if a.load_only else "skip" if a.skip_load else "auto"
    try:
        ok = asyncio.run(run(a.name, not a.no_check, a.speed, load))
    except* ScenarioError as group:  # raised inside the MCP session it arrives wrapped in a group
        print(f"erro: {_first_leaf(group)}", file=sys.stderr)
        sys.exit(2)
    if not a.no_check and load != "only":
        print("== PASSOU" if ok else "== FALHOU")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

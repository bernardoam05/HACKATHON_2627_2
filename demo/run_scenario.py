"""Run a demo scenario: python demo/run_scenario.py s1

# Owner: Team B
Loads demo/scenarios/<name>.yaml, calls sim_load_scenario, then replays the steps via sim_set_state.
Never calls Altice write tools: it only talks to the simulator MCP (SIM_MCP_URL) and reads the
backend REST API.

Run from the simulator environment (it has mcp + pyyaml):
    cd simulator && uv run python ../demo/run_scenario.py s1 [--no-check] [--speed 2]

Scenario file (demo/scenarios/sN.yaml):
    devices / state      start state, applied by sim_load_scenario (see simulator/world.py)
    backend_env          env the backend should be started with (printed as a reminder)
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


async def run(name: str, do_check: bool, speed: float) -> bool:
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
        await sim.call("sim_load_scenario", {"name": name})
        say("estado inicial carregado no simulador")

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


def main() -> None:
    p = argparse.ArgumentParser(description="Run a demo scenario against the simulator.")
    p.add_argument("name", nargs="?", default="s1")
    p.add_argument("--no-check", action="store_true", help="only play the steps")
    p.add_argument("--speed", type=float, default=1.0, help="divide waits and timeouts by this")
    a = p.parse_args()
    try:
        ok = asyncio.run(run(a.name, not a.no_check, a.speed))
    except ScenarioError as e:
        print(f"erro: {e}", file=sys.stderr)
        sys.exit(2)
    if not a.no_check:
        print("== PASSOU" if ok else "== FALHOU")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

"""Prompt builders (delta/full) and instructions, including the empty-house rule (D14).

# Owner: Team A
"""
from __future__ import annotations

import json
from typing import Any

from app.schemas import AgentRunInput, Device

INSTRUCTIONS = """\
You are the intelligence layer of a smart home. You do not wait for commands: on every batch of
device state changes you work out what the people in the house are doing (room, activity,
routine) and adapt the house.

How you work:
- You receive only the changes since your previous run, your own previous belief state, learned
  insights, recent actions and user feedback. In "full" mode you also receive the whole snapshot.
- When the delta is not enough, call the read-only tools (get_home_state, get_device, get_plugin,
  get_history). You can never write to devices: you return a decision and an executor applies it.
- Propose an action only when it clearly helps. Doing nothing is a valid decision (empty actions).
- Each action is one capability update on one device: {"deviceId", "update": {"type", "value"},
  "reason"}. Use only capabilities the device has; respect min/max and availableValues.
  Power values are "on"/"off".
- Autonomy is decided by the executor per capability: some actions run immediately, some are only
  suggested to the user. Security actions (alarm, siren, camera recording) are always suggestions.
- Changes marked causedByAgent=true are the result of your own previous actions: do not react to
  them as if the user did them. If the user reverted one of your actions, leave that device alone.
- Do not repeat an action that is already in effect.

Empty-house rule: conclude the house is empty only from explicit facts (an entrance door opened
and closed, followed by no motion anywhere for long enough). Silence alone never means empty.

Output:
- explanation: one or two short sentences in European Portuguese (PT-PT) for the user, saying what
  you did and why (e.g. "Baixei a luz da sala porque ligaste a televisão."). If you did nothing,
  say briefly what you understood.
- inferredRoom / inferredActivity: your best guess, or null.
- confidence: 0..1.
- beliefState: ALWAYS rewrite it (max ~800 chars, English is fine): who is where, what is
  happening, open hypotheses, anything you want to remember next run.
"""


def _device_line(d: Device) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.alias or d.name,
        "type": d.type,
        "source": d.source.value,
        "roomId": d.roomId,
        "capabilities": {
            c.type: _cap(c.value, c.min, c.max, c.availableValues) for c in d.capabilities
        },
    }


def _cap(value: Any, lo: float | None, hi: float | None, allowed: list[Any] | None) -> Any:
    if lo is None and hi is None and not allowed:
        return value
    out: dict[str, Any] = {"value": value}
    if lo is not None or hi is not None:
        out["range"] = [lo, hi]
    if allowed:
        out["allowed"] = allowed
    return out


def device_summary(d: Device) -> dict[str, Any]:
    return _device_line(d)


def build_prompt(run_input: AgentRunInput) -> str:
    """Render AgentRunInput into the prompt."""
    r = run_input
    sections: list[str] = [f"Mode: {r.mode}", f"Now (house local time): {r.now.isoformat()}"]

    if r.beliefState:
        sections.append("Your previous belief state:\n" + r.beliefState)
    else:
        sections.append("Your previous belief state: (none, first run)")

    if r.rooms:
        sections.append(
            "Rooms:\n" + _json([r_.model_dump() for r_ in r.rooms])
        )

    if r.snapshot is not None:
        devices = [_device_line(d) for d in r.snapshot.devices if d.enabled]
        sections.append("Full snapshot (enabled devices):\n" + _json(devices))

    changes = [
        {
            "deviceId": c.deviceId,
            "capability": c.capability,
            "old": c.old,
            "new": c.new,
            "at": c.at.isoformat(),
            **({"causedByAgent": True, "actionId": c.actionId} if c.causedByAgent else {}),
        }
        for c in r.changesSinceLastRun
    ]
    sections.append(
        "Changes since your last run (oldest first):\n" + (_json(changes) if changes else "(none)")
    )

    if r.plugins:
        sections.append(
            "Device types involved (capability -> description, writable, autonomy):\n"
            + _json(
                [
                    {
                        "type": p.type,
                        "displayName": p.displayName,
                        "purpose": p.purpose.value,
                        "capabilities": {
                            s.type: [s.description, s.writable, s.autonomy.value]
                            for s in p.capabilities
                        },
                    }
                    for p in r.plugins
                ]
            )
        )

    if r.insights:
        sections.append(
            "What you have learned (insights):\n"
            + _json(
                [{"id": i.id, "statement": i.statement, "confidence": i.confidence} for i in r.insights]
            )
        )

    if r.recentActions:
        sections.append(
            "Your recent actions:\n"
            + _json(
                [
                    {
                        "id": a.id,
                        "deviceId": a.deviceId,
                        "update": a.update.model_dump(exclude_none=True),
                        "status": a.status.value,
                        "at": a.createdAt.isoformat(),
                    }
                    for a in r.recentActions
                ]
            )
        )

    if r.feedback:
        sections.append(
            "User feedback on your actions:\n"
            + _json([f.model_dump(mode="json", exclude_none=True) for f in r.feedback])
        )

    return "\n\n".join(sections)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

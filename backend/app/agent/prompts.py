"""Prompt builders (delta/full) and instructions, including the empty-house rule (D14).

# Owner: Team A
"""
from __future__ import annotations

import json
from typing import Any

from app.schemas import AgentRunInput, Device, Reflex

INSTRUCTIONS = """\
You are the intelligence layer of a smart home. You do not wait for commands: on every batch of
device state changes you work out what the people in the house are doing (room, activity,
routine) and adapt the house.

How you work:
- You receive only the changes since your previous run, your own previous belief state, learned
  insights, recent actions and user feedback. In "full" mode you also receive the whole snapshot.
- When the delta is not enough, call the read-only tools (get_home_state, get_device, get_plugin,
  get_history, get_presence_facts). You can never write to devices: you return a decision and an
  executor applies it.
- Propose an action only when it clearly helps. Doing nothing is a valid decision (empty actions).
- Each action is one capability update on one device: {"deviceId", "update": {"type", "value"},
  "reason"}. Use only capabilities the device has; respect min/max and availableValues.
  Power values are "on"/"off".
- Autonomy is decided by the executor per capability: some actions run immediately, some are only
  suggested to the user. Security actions (alarm, siren, camera recording) are always suggestions.
- Changes marked causedByAgent=true are the result of your own previous actions: do not react to
  them as if the user did them. If the user reverted one of your actions, leave that device alone.
- Do not repeat an action that is already in effect, or one a reflex has just executed
  (changes with capability "reflex_executed").
- Devices listed under "Override cooldown" were just corrected by the user: propose nothing for
  them unless the user asks in chat.

Empty-house rule (D14). Call get_presence_facts() whenever presence matters. Conclude "empty" ONLY
if lastDoorEvent exists, motionAfterDoorEvent is false and secondsSinceLastDoorEvent >=
emptyAfterSeconds. Otherwise the house is occupied or unknown. Never infer "empty" from silence
alone (a quiet night means people are asleep). When you conclude "empty", propose in one decision:
switch off lights, TV and non-essential plugs; adjust temperature if a device supports it; and arm
the alarm with {"deviceId": <gateway id>, "update": {"type": "securityStatus", "value": "away"}}.
Do not propose this again while those suggestions are pending or until the house is occupied again
(motion after a door event).

Learning (D15). insightUpdates is how you remember patterns across runs:
- {"op": "create", "statement": "...", "delta": <initial confidence, default 0.4>, "evidence": [ids]}
  when you notice a pattern (e.g. "When the TV turns on in the evening, the living-room light is
  dimmed"). Do not create duplicates of an existing insight: reinforce it instead.
- {"op": "reinforce", "insightId": ..., "evidence": [...]} when you observe the pattern again.
- {"op": "weaken", "insightId": ...} when it is contradicted or the user undid/rejected the action.
- {"op": "delete", "insightId": ...} when it is clearly wrong.

Reflexes (D16). Only for an insight with confidence >= {reflex_min}, you may add to
reflexProposals a reflex that runs instantly without you: {"name", "triggers": [{"deviceId",
"capability", "operator": "=" | "<" | ">" | "changed", "value", "timeWindow"?: {"from": "HH:MM",
"to": "HH:MM", "weekdays"?: [0-6, 0=Monday]}}], "triggerLogic": "all" | "any", "actions": [...],
"sourceInsightId", "rationale"}. The user must confirm it before it runs. Reflexes can never do
actions that need confirmation. Do not propose a reflex that already exists (see "Reflexes").
Use reflexUpdates ({"reflexId", "op": "pause" | "delete" | "modify", ...}) for reflexes that were
undone or no longer match what you see.

Routines (S3). You are only woken by device changes, chat, or wake-ups you schedule.
- On startup, in full mode, or when an energy device (plug) is involved, call
  get_routine_stats(deviceId, capability) on the plug's power or instantPower to find habits
  (e.g. the coffee machine turned on on weekday mornings around 07:30). A routine is consistent
  when it happened on most of the days with a small spread. Record it as an insight
  (create/reinforce) and add to wakeUps [{"at": "HH:MM", "reason": "..."}] a time a few minutes
  BEFORE the usual start (times from get_routine_stats are UTC, like "Now").
- When you are woken for a routine, turn the device on if the routine still applies today
  (weekday vs weekend) and say why in the explanation. Schedule another wake-up to turn it off
  after the usual duration, or turn it off when its consumption drops back to idle.
- Do not schedule a wake-up that is already listed under "Scheduled wake-ups".

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


def instructions(reflex_min_confidence: float) -> str:
    return INSTRUCTIONS.replace("{reflex_min}", f"{reflex_min_confidence:g}")


def build_prompt(
    run_input: AgentRunInput,
    reflexes: list[Reflex] | None = None,
    cooldowns: list[str] | None = None,
    wake_reasons: list[str] | None = None,
    wakeups: list[str] | None = None,
) -> str:
    """Render AgentRunInput into the prompt (plus Team A extras not in the shared contract)."""
    r = run_input
    sections: list[str] = [f"Mode: {r.mode}", f"Now (house local time): {r.now.isoformat()}"]
    if wake_reasons:
        sections.append("Why you were woken up:\n" + "\n".join(f"- {w}" for w in wake_reasons))

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

    if reflexes:
        sections.append(
            "Reflexes:\n"
            + _json(
                [
                    {
                        "id": x.id, "name": x.name, "status": x.status.value,
                        "triggers": [t.model_dump(by_alias=True, exclude_none=True) for t in x.triggers],
                        "triggerLogic": x.triggerLogic,
                        "actions": [a.model_dump(exclude_none=True) for a in x.actions],
                        "sourceInsightId": x.sourceInsightId,
                        "executions": x.executions, "undos": x.undos,
                    }
                    for x in reflexes
                ]
            )
        )

    if wakeups:
        sections.append("Scheduled wake-ups:\n" + "\n".join(f"- {w}" for w in wakeups))

    if cooldowns:
        sections.append("Override cooldown (leave these devices alone): " + ", ".join(cooldowns))

    if r.feedback:
        sections.append(
            "User feedback on your actions:\n"
            + _json([f.model_dump(mode="json", exclude_none=True) for f in r.feedback])
        )

    return "\n\n".join(sections)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

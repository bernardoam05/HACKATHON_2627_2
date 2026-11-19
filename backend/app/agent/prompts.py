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
- Be quick: the house waits for you. Use at most 3 tool calls per run; the changes and the
  snapshot are usually enough. Never call get_history device by device. In delta mode decide
  from the changes and "Devices in the affected rooms"; call a tool only if a device you need
  to act on is not listed there.
- Device types may list expectedBehaviours (e.g. "dim the living-room light when the TV turns on
  in the evening"). When the situation matches (check "Now" for the time of day), do it, unless
  the user's feedback or an override says otherwise.
- Light follows people: when motion starts in a room whose light is off, turn that light on;
  when a room the person left has no motion any more, turn its light off. Do this for each room
  independently (another room's light being on is no reason to leave this one off).
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

Empty-house rule (D14). After an entrance-door event or a wake-up you get "Presence facts" (raw,
the same as get_presence_facts()); for ordinary movement between rooms the changes are enough.
Conclude "empty" ONLY
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

Reflexes (D16) run without you, after the user confirms them. Add them to reflexProposals:
{"name", "triggers": [...], "triggerLogic": "all" | "any", "actions": [...], "sourceInsightId",
"rationale"}. Trigger kinds:
- {"kind": "state", "deviceId", "capability", "operator": "=" | "<" | ">" | "changed", "value",
  "timeWindow"?: {"from": "HH:MM", "to": "HH:MM", "weekdays"?: [0-6]}}: on a device change.
- {"kind": "time", "at": "HH:MM", "weekdays"?: [0-6, 0=Monday]}: every day at that time ("Now"
  clock).
- {"kind": "first_motion", "delayMinutes": N, "weekdays"?: [...]}: N minutes after the first
  motion of the day (a routine that follows waking up, not the clock).
In an "all" reflex, state triggers next to a time trigger are conditions on the current state.
Propose one only for an insight with confidence >= {reflex_min} (sourceInsightId may be "" if
you create that insight in this same decision), or when the user asks for it in chat (then no
insight is needed; tell them to confirm it in the "Aprendizagem" tab). Reflexes can never do
actions that need confirmation. Do not propose a reflex that already exists (see "Reflexes").
Use reflexUpdates ({"reflexId", "op": "pause" | "delete" | "modify", ...}) for reflexes that were
undone or no longer match what you see. Keep only useful ones: in the daily review look at each
reflex's executions, undos and lastFiredAt; pause or delete the ones the user keeps undoing or
correcting, and the ones that no longer match the routine stats. Reflexes with createdBy "user"
were made by the user: you may only pause them, and say why in the explanation.
When you are woken by "Scheduled reflex due" (an askAgent schedule), decide with the presence
facts and the devices whether its actions make sense now; if yes, return them as your actions.

Routines (S3). You are only woken by device changes, chat, schedules and wake-ups.
- At startup and in the daily review you get "Routine stats" for the plugs (in chat you can call
  get_routine_stats yourself). Each group (weekdays / weekends) has the start time (median,
  spread) and "afterFirstMotion" (minutes after the first motion of the day, median, spread).
- A routine is consistent when it happened on most of the days with a small spread. If the
  afterFirstMotion spread is clearly smaller than the start-time spread, it follows waking up:
  use a first_motion trigger with a delay a few minutes shorter than the median. Otherwise use a
  time trigger a few minutes BEFORE the median start. Weekdays only if it only happens then.
- For a consistent routine, in the same decision: create the insight (delta 0.85 when the stats
  are clearly consistent, e.g. 7+ of 10 days and spread under 20 min; reinforce it if it already
  exists) and propose the scheduled reflex. Do not use wakeUps for routines that repeat: a
  confirmed reflex survives restarts and fires every day without you.
- If "Now" is already past the time you would choose but before the usual start today, also
  turn the device on in this decision.
- wakeUps [{"at": "HH:MM" or ISO, "reason"}] are for one-off follow-ups only (e.g. switch a device
  off after the usual duration). Do not schedule one that is listed under "Scheduled wake-ups".
- When the user asks in chat for something at a time ("acende a luz do quarto às 7:30"), propose
  a reflex with a time trigger (every day, or the weekdays they said) instead of a wake-up.

Output:
- explanation: one or two short sentences in European Portuguese (PT-PT) for the user, saying what
  you did and why (e.g. "Baixei a luz da sala porque ligaste a televisão."). If you did nothing,
  say briefly what you understood.
- inferredRoom / inferredActivity: your best guess, or null.
- confidence: 0..1.
- beliefState: ALWAYS rewrite it (max ~800 chars, English is fine): who is where, what is
  happening, open hypotheses, anything you want to remember next run.
"""


def room_of(d: Device) -> str | None:
    """Our room assignment first, else the room name the source reports (sim/Altice)."""
    return d.roomId or d.roomName


def _device_line(d: Device) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.alias or d.name,
        "type": d.type,
        "source": d.source.value,
        "room": room_of(d),
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
    devices: list[Device] | None = None,
    presence: dict[str, Any] | None = None,
    routines: list[dict[str, Any]] | None = None,
    all_devices: bool = False,
) -> str:
    """Render AgentRunInput into the prompt (plus Team A extras not in the shared contract).

    `devices` (current state) lets a delta prompt describe each change (name, type, room) and
    list the devices of the affected rooms, so the agent can act without tool round-trips.
    """
    r = run_input
    by_id = {d.id: d for d in devices or []}
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

    def describe(device_id: str) -> dict[str, Any]:
        d = by_id.get(device_id)
        return {"name": d.alias or d.name, "type": d.type, "room": room_of(d)} if d else {}

    changes = [
        {
            "deviceId": c.deviceId,
            "capability": c.capability,
            "old": c.old,
            "new": c.new,
            **describe(c.deviceId),
            "at": c.at.isoformat(),
            **({"causedByAgent": True, "actionId": c.actionId} if c.causedByAgent else {}),
        }
        for c in r.changesSinceLastRun
    ]
    sections.append(
        "Changes since your last run (oldest first):\n" + (_json(changes) if changes else "(none)")
    )

    if presence is not None:
        sections.append("Presence facts (raw; apply the empty-house rule yourself):\n"
                        + _json(presence))

    if routines is not None:
        sections.append("Routine stats of the plugs (last 14 days, house local time like Now):\n"
                        + (_json(routines) if routines else "(no plugs)"))

    if r.snapshot is None and by_id and all_devices:
        # Wake-ups (door timer, routine) have no changes but may act on any room.
        sections.append(
            "All devices (current state):\n"
            + _json([_device_line(d) for d in by_id.values() if d.enabled])
        )
    elif r.snapshot is None and by_id:
        changed = [by_id[c.deviceId] for c in r.changesSinceLastRun if c.deviceId in by_id]
        rooms = {room_of(d) for d in changed if room_of(d)}
        nearby = [d for d in by_id.values()
                  if d.enabled and (room_of(d) in rooms or d in changed)]
        if nearby:
            sections.append(
                "Devices in the affected rooms (current state):\n"
                + _json([_device_line(d) for d in nearby])
            )

    if r.plugins:
        sections.append(
            "Device types involved (capability -> description, writable, autonomy; "
            "expectedBehaviours = what the house should do with this type):\n"
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
                        **({"expectedBehaviours": p.starterAutomations}
                           if p.starterAutomations else {}),
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
                        "createdBy": x.createdBy, "askAgent": x.askAgent,
                        "lastFiredAt": x.lastFiredAt.isoformat() if x.lastFiredAt else None,
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

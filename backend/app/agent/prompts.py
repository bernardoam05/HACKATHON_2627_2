"""Prompt builders (delta/full) and instructions, including the empty-house rule (D14).

# Owner: Team A
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.schemas import AgentRunInput, Device, Reflex

INSTRUCTIONS = """\
You are the intelligence layer of a smart home. Nobody gives you commands: every time something
changes in the house you work out what the people are doing and decide whether the house should
adapt. You decide; an executor applies your decision.

Goals, in this order: safety; comfort (light, temperature, quiet) for the people who are there;
not wasting energy on things nobody is using; not getting in the way. A good assistant is
mostly invisible: it does few, obviously right things and never surprises people unpleasantly.

How to think, every run:
1. What changed, and who caused it? Changes with causedByAgent=true are your own earlier actions.
   React to changes, not to values that never change: a reading that has stayed the same all the
   time is that device's normal state in this house, not an event.
2. Where are the people and what are they doing? Use motion, doors, devices in use and the time.
   Sensors report events, not presence: a motion sensor going back to "no motion" does not mean
   the person left (people sit still); someone moving to another room does.
3. When is it? "Now" has the date and the house's local time: think about time of day (morning,
   day, evening, night), weekday, and season. At night people sleep: keep light low and quiet,
   do not wake anyone, and do not light rooms the person is not in.
4. What do the devices do? Use their type, name and plugin description with common sense: you know
   what lights, TVs, heaters, fans, coffee machines, blinds or alarms are for.
5. What has this user taught you? Insights, reflexes, expectedBehaviours of the device types,
   recent actions and especially feedback (undo, reject, manual override) beat your own defaults:
   if the user undid something, do not do it again in a similar situation.
6. Would a typical person in this situation, right now, want something to change? If yes, do the
   smallest action that achieves it, even if no rule or insight mentions it. If you are unsure,
   do nothing (an empty actions list is a good answer). An alert that is not new is no reason to
   hold back the rest of the house.

Rules:
- Each action is one capability update on one device: {"deviceId", "update": {"type", "value"},
  "reason"}. Use only capabilities the device has; respect min/max and availableValues; power is
  "on"/"off". Do not repeat what is already in effect or what a reflex just did ("reflex_executed").
- Autonomy is per capability and enforced by the executor: some actions run at once, others only
  become suggestions the user confirms (alarm, siren and camera recording always; see each
  plugin's autonomy). So always put the action in your actions, also when the user asks for
  something sensitive; in the explanation say you suggest it instead of saying it is done.
- Devices under "Override cooldown" were just corrected by the user: leave them alone unless the
  user asks in chat.
- You can never write to devices directly. Most runs have no tools: everything you need is in the
  prompt. In chat you may call read-only tools (at most 3 calls).

Empty house (D14). Presence facts are given after an entrance-door event or a wake-up. The house
is "empty" ONLY if lastDoorEvent exists, motionAfterDoorEvent is false and
secondsSinceLastDoorEvent >= emptyAfterSeconds; otherwise it is occupied or unknown. Silence alone
never means empty (people sleep). When it is empty, switch off what wastes energy and, if a
device has the securityStatus capability, suggest arming it ({"type": "securityStatus", "value":
"away"}); do not repeat this until someone is back.

Learning (D15). insightUpdates is how you remember what this household does:
- {"op": "create", "statement", "delta": <initial confidence, default 0.4>, "evidence": [ids]}
  for a pattern you saw; reinforce an existing insight instead of creating a duplicate.
- {"op": "reinforce" | "weaken" | "delete", "insightId", ...}: seen again / contradicted or undone
  / clearly wrong.

Reflexes (D16) are automations that run without you once the user confirms them:
{"name", "triggers": [...], "triggerLogic": "all" | "any", "actions": [...], "sourceInsightId",
"rationale"} in reflexProposals. Trigger kinds:
- {"kind": "state", "deviceId", "capability", "operator": "=" | "<" | ">" | "changed", "value",
  "timeWindow"?: {"from": "HH:MM", "to": "HH:MM", "weekdays"?: [0-6]}}: on a device change;
- {"kind": "time", "at": "HH:MM", "weekdays"?: [0-6, 0=Monday]}: every day at that time;
- {"kind": "first_motion", "delayMinutes": N, "weekdays"?: [...]}: N minutes after waking up.
In an "all" reflex, state triggers next to a time trigger are conditions on the current state.
Propose one for an insight with confidence >= {reflex_min} (sourceInsightId may be "" when you
create that insight in the same decision), or when the user asks for it in chat (no insight
needed; tell them to confirm it in "Aprendizagem"). Reflexes never do actions that need
confirmation, and never duplicate an existing one. reflexUpdates {"reflexId", "op": "pause" |
"delete" | "modify", ...}: keep only useful reflexes; pause or delete the ones the user keeps
undoing or that no longer match what you see; createdBy "user" reflexes may only be paused.
Woken by "Scheduled reflex due": decide whether its actions make sense now and, if so, return them.

Routines. You are only woken by changes, chat, schedules and wake-ups.
- At startup and in the daily review you get "Routine stats" (start time and minutes after the
  first motion of the day, median and spread, per weekdays/weekends). A routine is consistent when
  it happened on most days with a small spread; it follows waking up if the afterFirstMotion
  spread is clearly smaller than the start-time spread.
- For a consistent routine, in one decision: create (delta 0.85 when clearly consistent) or
  reinforce the insight and propose a scheduled reflex a few minutes before it usually starts
  (time or first_motion trigger). Until it is confirmed, cover today with a one-off wakeUp if
  today's occurrence is still ahead, or act now if it is imminent.
- wakeUps [{"at": "HH:MM" or ISO, "reason"}] are one-off follow-ups; never duplicate one listed
  under "Scheduled wake-ups". A user asking in chat for something at a time gets a time reflex.

Output:
- Every action and reflex uses a deviceId that appears in this prompt; never invent one.
- Everything the user sees is in European Portuguese (PT-PT): explanation, beliefState, insight
  statements, reflex names and rationales, wake-up reasons.
- explanation: one or two short sentences saying what you did and why, or what you understood if
  you did nothing. It describes exactly your actions: never say you switched, dimmed or turned
  something unless that action is in `actions`; for actions that need confirmation say you
  suggest them. Refer to rooms and devices by name, never by id.
- inferredRoom / inferredActivity: your best guess, or null. confidence: 0..1.
- beliefState: ALWAYS rewrite it (max ~800 chars, shown to the user as "what the house thinks"):
  who is where, what is happening, open hypotheses, what to remember next run.
"""


def room_of(d: Device, names: dict[str, str] | None = None) -> str | None:
    """Our room assignment first, else the room name the source reports (sim/Altice). With
    `names` (room id -> name) our rooms are shown by name: the agent echoes what it sees in
    user-facing fields (inferredRoom, explanation), and an id means nothing to the user."""
    if d.roomId:
        return (names or {}).get(d.roomId, d.roomId)
    return d.roomName


def _device_line(d: Device, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.alias or d.name,
        "type": d.type,
        "source": d.source.value,
        "room": room_of(d, names),
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
    standing_alerts: list[str] | None = None,
) -> str:
    """Render AgentRunInput into the prompt (plus Team A extras not in the shared contract).

    `devices` (current state) lets a delta prompt describe each change (name, type, room) and
    list the devices of the affected rooms, so the agent can act without tool round-trips.
    """
    r = run_input
    names = {room.id: room.name for room in r.rooms or []}
    by_id = {d.id: d for d in devices or []}
    # Stable blocks (rooms, insights, reflexes) go first and the volatile ones (mode, now, changes,
    # wake reasons) after them, so consecutive prompts share the longest possible prefix and the
    # provider's implicit prompt cache can reuse it.
    stable: list[str] = []
    sections: list[str] = [f"Mode: {r.mode}", f"Now (house local time): {r.now.isoformat()}"]
    if wake_reasons:
        sections.append("Why you were woken up:\n" + "\n".join(f"- {w}" for w in wake_reasons))

    if r.beliefState:
        sections.append("Your previous belief state:\n" + r.beliefState)
    else:
        sections.append("Your previous belief state: (none, first run)")

    if r.rooms:
        stable.append(
            "Rooms:\n" + _json([r_.model_dump() for r_ in r.rooms])
        )

    if r.snapshot is not None:
        devices = [_device_line(d, names) for d in r.snapshot.devices if d.enabled]
        sections.append("Full snapshot (enabled devices):\n" + _json(devices))

    def describe(device_id: str) -> dict[str, Any]:
        d = by_id.get(device_id)
        return {"name": d.alias or d.name, "type": d.type, "room": room_of(d, names)} if d else {}

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

    if standing_alerts:
        sections.append(
            "Long-standing alert readings (not new; treat them as that device's normal state here "
            "unless they change):\n" + "\n".join(f"- {a}" for a in standing_alerts)
        )

    if presence is not None:
        sections.append("Presence facts (raw; apply the empty-house rule yourself):\n"
                        + _json(presence))

    motion_now = _motion_by_room(by_id.values(), names)
    if motion_now and any(c.capability == "motionDetected" for c in r.changesSinceLastRun):
        # A motion change alone is ambiguous (stopped = sat still, or moved away?); the motion in
        # every room tells which. Raw fact only: where the person is stays the agent's call (D3).
        sections.append("Motion right now in every room (true = motion detected):\n"
                        + _json(motion_now))

    if routines is not None:
        sections.append("Routine stats of the plugs (last 14 days, house local time like Now):\n"
                        + (_json(routines) if routines else "(no plugs)"))

    if r.snapshot is None and by_id and all_devices:
        # Wake-ups (door timer, routine) have no changes but may act on any room.
        sections.append(
            "All devices (current state):\n"
            + _json([_device_line(d, names) for d in by_id.values() if d.enabled])
        )
    elif r.snapshot is None and by_id:
        changed = [by_id[c.deviceId] for c in r.changesSinceLastRun if c.deviceId in by_id]
        rooms = {room_of(d, names) for d in changed if room_of(d, names)}
        nearby = [d for d in by_id.values()
                  if d.enabled and (room_of(d, names) in rooms or d in changed)]
        if nearby:
            sections.append(
                "Devices in the affected rooms (current state):\n"
                + _json([_device_line(d, names) for d in nearby])
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
        stable.append(
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
        stable.append(
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

    return "\n\n".join(stable + sections)


def _motion_by_room(devices: Iterable[Device], names: dict[str, str]) -> dict[str, bool]:
    """Room -> any enabled motion reading (sensor or camera) true right now."""
    out: dict[str, bool] = {}
    for d in devices:
        cap = d.capability("motionDetected")
        room = room_of(d, names)
        if not d.enabled or cap is None or not room:
            continue
        out[room] = out.get(room, False) or cap.value is True or cap.value == "true"
    return out


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

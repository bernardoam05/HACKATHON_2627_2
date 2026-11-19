"""Anti-loop (D8).

# Owner: Team A
"""
from __future__ import annotations

from datetime import timedelta

from app.schemas import ActionRecord, ActionStatus, StateChange

ANTILOOP_WINDOW = timedelta(seconds=15)


def _num(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, int | float) else None


def _matches(change: StateChange, action: ActionRecord) -> bool:
    if action.status != ActionStatus.executed:
        return False
    if action.deviceId != change.deviceId or action.update.type != change.capability:
        return False
    delta = change.at - action.createdAt
    if delta < timedelta(seconds=-1) or delta > ANTILOOP_WINDOW:
        return False
    if change.new == action.expectedValue:
        return True
    # Fades: an intermediate value moving from `old` towards the expected value also counts.
    old, new, target = _num(change.old), _num(change.new), _num(action.expectedValue)
    if old is None or new is None or target is None:
        return False
    return min(old, target) <= new <= max(old, target)


def tag_agent_changes(
    changes: list[StateChange], recent_actions: list[ActionRecord]
) -> list[StateChange]:
    """Set causedByAgent when device+capability+expected value match an action within ~15 s.

    Mutates and returns `changes`. The most recent matching action wins.
    """
    ordered = sorted(recent_actions, key=lambda a: a.createdAt, reverse=True)
    for change in changes:
        for action in ordered:
            if _matches(change, action):
                change.causedByAgent = True
                change.actionId = action.id
                break
    return changes

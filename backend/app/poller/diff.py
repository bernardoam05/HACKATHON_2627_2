"""Pure snapshot diff.

# Owner: Team A
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.schemas import Device, StateChange, StateSnapshot

# Pseudo-capability used for device added/removed changes: old/new are "absent"/"present".
DEVICE_PRESENCE = "_device"


@dataclass
class SnapshotDiff:
    changes: list[StateChange] = field(default_factory=list)
    added: list[Device] = field(default_factory=list)
    removed: list[Device] = field(default_factory=list)


def new_id() -> str:
    return uuid.uuid4().hex


def diff_snapshots(prev: StateSnapshot | None, curr: StateSnapshot) -> SnapshotDiff:
    """Capability value changes plus added/removed devices. `prev=None` means no baseline: nothing changed."""
    out = SnapshotDiff()
    if prev is None:
        return out
    at = curr.takenAt
    before = {d.id: d for d in prev.devices}
    after = {d.id: d for d in curr.devices}

    for dev_id, dev in after.items():
        old_dev = before.get(dev_id)
        if old_dev is None:
            out.added.append(dev)
            out.changes.append(_change(dev_id, DEVICE_PRESENCE, "absent", "present", at))
            continue
        old_caps = {c.type: c.value for c in old_dev.capabilities}
        for cap in dev.capabilities:
            if cap.type in old_caps and old_caps[cap.type] != cap.value:
                out.changes.append(_change(dev_id, cap.type, old_caps[cap.type], cap.value, at))
            elif cap.type not in old_caps:
                out.changes.append(_change(dev_id, cap.type, None, cap.value, at))

    for dev_id, dev in before.items():
        if dev_id not in after:
            out.removed.append(dev)
            out.changes.append(_change(dev_id, DEVICE_PRESENCE, "present", "absent", at))
    return out


def diff(prev: StateSnapshot | None, curr: StateSnapshot) -> list[StateChange]:
    """Return StateChange list between two StateSnapshots; also detect device added/removed."""
    return diff_snapshots(prev, curr).changes


def _change(device_id: str, capability: str, old: object, new: object, at) -> StateChange:
    return StateChange(
        id=new_id(), deviceId=device_id, capability=capability, old=old, new=new, at=at
    )

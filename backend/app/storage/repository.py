"""Repository over SQLite.

# Owner: Team A
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypeVar

import aiosqlite
from pydantic import BaseModel

from app.schemas import (
    ActionRecord,
    Decision,
    DevicePlugin,
    Insight,
    Reflex,
    Room,
    StateChange,
    StateSnapshot,
    UserFeedback,
)

M = TypeVar("M", bound=BaseModel)


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes are treated as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def ts(dt: datetime) -> str:
    return to_utc(dt).isoformat()


def parse_ts(value: str) -> str:
    """Normalise a user-supplied ISO timestamp (e.g. `since` query param) for comparisons."""
    return ts(datetime.fromisoformat(value.replace("Z", "+00:00")))


class Repository:
    """Typed CRUD for all tables; implements HistoryService and the persistence side of ActionService."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

    async def close(self) -> None:
        await self._db.close()

    async def _rows(self, model: type[M], sql: str, params: tuple = ()) -> list[M]:
        async with self._db.execute(sql, params) as cur:
            return [model.model_validate_json(r["data"]) for r in await cur.fetchall()]

    async def _one(self, model: type[M], sql: str, params: tuple = ()) -> M | None:
        rows = await self._rows(model, sql, params)
        return rows[0] if rows else None

    # --- snapshots --------------------------------------------------------------------------

    async def save_snapshot(self, snap: StateSnapshot) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO snapshots(id, ts, data) VALUES (?, ?, ?)",
            (snap.id, ts(snap.takenAt), snap.model_dump_json()),
        )
        await self._db.commit()

    async def latest_snapshot(self) -> StateSnapshot | None:
        return await self._one(
            StateSnapshot, "SELECT data FROM snapshots ORDER BY ts DESC LIMIT 1"
        )

    # --- changes (HistoryService) -----------------------------------------------------------

    async def add_changes(self, changes: list[StateChange]) -> None:
        if not changes:
            return
        await self._db.executemany(
            "INSERT OR REPLACE INTO changes(id, ts, device_id, capability, caused_by_agent, data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (c.id, ts(c.at), c.deviceId, c.capability, int(c.causedByAgent), c.model_dump_json())
                for c in changes
            ],
        )
        await self._db.commit()

    async def list_changes(
        self, device_id: str | None, since: str | None, limit: int
    ) -> list[StateChange]:
        """Most recent first."""
        where, params = self._change_filter(device_id, None, since)
        return await self._rows(
            StateChange,
            f"SELECT data FROM changes {where} ORDER BY ts DESC LIMIT ?",
            (*params, limit),
        )

    async def changes_between(
        self,
        since: datetime | None,
        until: datetime | None = None,
        device_id: str | None = None,
        capability: str | None = None,
    ) -> list[StateChange]:
        """Oldest first; `since` exclusive, `until` inclusive."""
        where, params = self._change_filter(
            device_id, capability, ts(since) if since else None, exclusive=True
        )
        if until is not None:
            where += (" AND " if where else "WHERE ") + "ts <= ?"
            params.append(ts(until))
        return await self._rows(
            StateChange, f"SELECT data FROM changes {where} ORDER BY ts ASC", tuple(params)
        )

    @staticmethod
    def _change_filter(
        device_id: str | None, capability: str | None, since: str | None, exclusive: bool = False
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        if capability:
            clauses.append("capability = ?")
            params.append(capability)
        if since:
            clauses.append("ts > ?" if exclusive else "ts >= ?")
            params.append(since if exclusive else parse_ts(since))
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    # --- decisions --------------------------------------------------------------------------

    async def save_decision(self, d: Decision) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO decisions(id, ts, data) VALUES (?, ?, ?)",
            (d.id, ts(d.createdAt), d.model_dump_json()),
        )
        await self._db.commit()

    async def list_decisions(self, limit: int) -> list[Decision]:
        return await self._rows(
            Decision, "SELECT data FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)
        )

    # --- actions ----------------------------------------------------------------------------

    async def save_action(self, a: ActionRecord) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO actions(id, ts, device_id, status, data) VALUES (?, ?, ?, ?, ?)",
            (a.id, ts(a.createdAt), a.deviceId, a.status.value, a.model_dump_json()),
        )
        await self._db.commit()

    async def get_action(self, action_id: str) -> ActionRecord | None:
        return await self._one(ActionRecord, "SELECT data FROM actions WHERE id = ?", (action_id,))

    async def list_actions(self, status: str | None, limit: int) -> list[ActionRecord]:
        if status:
            return await self._rows(
                ActionRecord,
                "SELECT data FROM actions WHERE status = ? ORDER BY ts DESC LIMIT ?",
                (status, limit),
            )
        return await self._rows(
            ActionRecord, "SELECT data FROM actions ORDER BY ts DESC LIMIT ?", (limit,)
        )

    async def actions_since(self, since: datetime) -> list[ActionRecord]:
        return await self._rows(
            ActionRecord,
            "SELECT data FROM actions WHERE ts >= ? ORDER BY ts ASC",
            (ts(since),),
        )

    # --- feedback ---------------------------------------------------------------------------

    async def add_feedback(self, f: UserFeedback) -> None:
        await self._db.execute(
            "INSERT INTO feedback(ts, action_id, data) VALUES (?, ?, ?)",
            (ts(f.at), f.actionId, f.model_dump_json()),
        )
        await self._db.commit()

    async def recent_feedback(self, limit: int = 20) -> list[UserFeedback]:
        return await self._rows(
            UserFeedback, "SELECT data FROM feedback ORDER BY ts DESC LIMIT ?", (limit,)
        )

    # --- rooms ------------------------------------------------------------------------------

    async def list_rooms(self) -> list[Room]:
        return await self._rows(Room, "SELECT data FROM rooms ORDER BY rowid")

    async def save_room(self, room: Room) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO rooms(id, data) VALUES (?, ?)", (room.id, room.model_dump_json())
        )
        await self._db.commit()

    async def delete_room(self, room_id: str) -> bool:
        cur = await self._db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await self._db.execute(
            "UPDATE device_overrides SET room_id = NULL WHERE room_id = ?", (room_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    # --- device overrides -------------------------------------------------------------------

    async def get_overrides(self) -> dict[str, dict]:
        async with self._db.execute("SELECT * FROM device_overrides") as cur:
            rows = await cur.fetchall()
        return {
            r["device_id"]: {
                "alias": r["alias"],
                "roomId": r["room_id"],
                "enabled": None if r["enabled"] is None else bool(r["enabled"]),
            }
            for r in rows
        }

    async def set_override(self, device_id: str, **fields: object) -> None:
        """Partial update; only keys passed are changed. Keys: alias, roomId, enabled."""
        current = (await self.get_overrides()).get(
            device_id, {"alias": None, "roomId": None, "enabled": None}
        )
        current.update(fields)
        enabled = current["enabled"]
        await self._db.execute(
            "INSERT OR REPLACE INTO device_overrides(device_id, alias, room_id, enabled)"
            " VALUES (?, ?, ?, ?)",
            (device_id, current["alias"], current["roomId"], None if enabled is None else int(enabled)),
        )
        await self._db.commit()

    # --- plugins ----------------------------------------------------------------------------

    async def get_plugin(self, type: str) -> DevicePlugin | None:
        return await self._one(DevicePlugin, "SELECT data FROM plugins WHERE type = ?", (type,))

    async def list_plugins(self) -> list[DevicePlugin]:
        return await self._rows(DevicePlugin, "SELECT data FROM plugins ORDER BY type")

    async def save_plugin(self, p: DevicePlugin) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO plugins(type, data) VALUES (?, ?)", (p.type, p.model_dump_json())
        )
        await self._db.commit()

    # --- belief state -----------------------------------------------------------------------

    async def save_belief(self, text: str, mode: str, at: datetime) -> None:
        await self._db.execute(
            "INSERT INTO belief_state(ts, mode, text) VALUES (?, ?, ?)", (ts(at), mode, text)
        )
        await self._db.commit()

    async def latest_belief(self) -> tuple[str, str, datetime] | None:
        """(text, mode, at) of the latest run."""
        async with self._db.execute(
            "SELECT ts, mode, text FROM belief_state ORDER BY id DESC LIMIT 1"
        ) as cur:
            r = await cur.fetchone()
        return (r["text"], r["mode"], datetime.fromisoformat(r["ts"])) if r else None

    async def last_full_run_at(self) -> datetime | None:
        async with self._db.execute(
            "SELECT ts FROM belief_state WHERE mode = 'full' ORDER BY id DESC LIMIT 1"
        ) as cur:
            r = await cur.fetchone()
        return datetime.fromisoformat(r["ts"]) if r else None

    async def runs_since_full(self) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) AS n FROM belief_state WHERE id > COALESCE("
            "(SELECT MAX(id) FROM belief_state WHERE mode = 'full'), 0)"
        ) as cur:
            r = await cur.fetchone()
        return int(r["n"])

    # --- insights ---------------------------------------------------------------------------

    async def save_insight(self, i: Insight) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO insights(id, status, confidence, data) VALUES (?, ?, ?, ?)",
            (i.id, i.status.value, i.confidence, i.model_dump_json()),
        )
        await self._db.commit()

    async def get_insight(self, insight_id: str) -> Insight | None:
        return await self._one(Insight, "SELECT data FROM insights WHERE id = ?", (insight_id,))

    async def list_insights(self, limit: int | None = None) -> list[Insight]:
        """Active only, highest confidence first."""
        sql = "SELECT data FROM insights WHERE status = 'active' ORDER BY confidence DESC"
        if limit is not None:
            return await self._rows(Insight, sql + " LIMIT ?", (limit,))
        return await self._rows(Insight, sql)

    async def delete_insight(self, insight_id: str) -> bool:
        cur = await self._db.execute("DELETE FROM insights WHERE id = ?", (insight_id,))
        await self._db.commit()
        return cur.rowcount > 0

    # --- reflexes ---------------------------------------------------------------------------

    async def save_reflex(self, r: Reflex) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO reflexes(id, status, data) VALUES (?, ?, ?)",
            (r.id, r.status.value, r.model_dump_json(by_alias=True)),
        )
        await self._db.commit()

    async def get_reflex(self, reflex_id: str) -> Reflex | None:
        return await self._one(Reflex, "SELECT data FROM reflexes WHERE id = ?", (reflex_id,))

    async def list_reflexes(self, status: str | None = None) -> list[Reflex]:
        if status:
            return await self._rows(
                Reflex, "SELECT data FROM reflexes WHERE status = ? ORDER BY rowid", (status,)
            )
        return await self._rows(
            Reflex, "SELECT data FROM reflexes WHERE status != 'deleted' ORDER BY rowid"
        )

    async def log_reflex_execution(self, reflex_id: str, action_ids: list[str], at: datetime) -> None:
        await self._db.execute(
            "INSERT INTO reflex_executions(ts, reflex_id, action_ids) VALUES (?, ?, ?)",
            (ts(at), reflex_id, json.dumps(action_ids)),
        )
        await self._db.commit()

"""SQLite connection and schema.

Every row stores the full Pydantic model as JSON in `data`; the extra columns only exist for
filtering/sorting. Timestamps are UTC ISO-8601 strings, so they sort lexicographically.

# Owner: Team A
"""
from __future__ import annotations

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS changes (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, device_id TEXT NOT NULL, capability TEXT NOT NULL,
    caused_by_agent INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_changes_ts ON changes(ts);
CREATE INDEX IF NOT EXISTS ix_changes_device ON changes(device_id, capability, ts);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, device_id TEXT NOT NULL, status TEXT NOT NULL,
    data TEXT NOT NULL, prev_value TEXT);
CREATE INDEX IF NOT EXISTS ix_actions_ts ON actions(ts);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, action_id TEXT NOT NULL,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS device_overrides (
    device_id TEXT PRIMARY KEY, alias TEXT, room_id TEXT, enabled INTEGER);
CREATE TABLE IF NOT EXISTS plugins (
    type TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS belief_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mode TEXT NOT NULL,
    text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, confidence REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflexes (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflex_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, reflex_id TEXT NOT NULL,
    action_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS device_cooldowns (
    device_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cooldown_defaults (
    device_id TEXT PRIMARY KEY, minutes REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    """Open the database (":memory:" allowed) and create all tables."""
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.executescript(SCHEMA)
    await conn.commit()
    return conn

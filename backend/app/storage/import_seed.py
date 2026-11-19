"""Seed history importer: python -m app.storage.import_seed demo/seed/history.jsonl

Options:
  --db PATH          database (default: DB_PATH from settings)
  --align-to-today   shift all timestamps by whole days so the latest seed day is today
                     (times of day are kept, so routines stay at the same hour)

# Owner: Team A
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.schemas import StateChange

from .db import init_db
from .repository import Repository, to_utc

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    imported: int
    skipped: int
    shift_days: int = 0


def parse_lines(lines: list[str]) -> tuple[list[StateChange], int]:
    """Valid StateChanges plus the number of skipped lines. Lines without `id` get a stable id
    derived from their content, so re-importing the same file is idempotent."""
    changes: list[StateChange] = []
    skipped = 0
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
            if not data.get("id"):
                data["id"] = "seed-" + hashlib.sha1(line.encode()).hexdigest()[:16]
            change = StateChange.model_validate(data)
        except (json.JSONDecodeError, ValidationError, AttributeError) as e:
            log.warning("line %d skipped: %s", n, e)
            skipped += 1
            continue
        changes.append(change.model_copy(update={"at": to_utc(change.at)}))
    return changes, skipped


def align_to_today(changes: list[StateChange], today: datetime | None = None) -> int:
    """Shift in place by whole days so the latest change falls on `today`. Returns the shift."""
    if not changes:
        return 0
    today = to_utc(today or datetime.now(UTC))
    latest = max(c.at for c in changes)
    days = (today.date() - latest.date()).days
    if days:
        for c in changes:
            c.at = c.at + timedelta(days=days)
    return days


async def import_seed(
    path: str, db_path: str | None = None, align: bool = False, repo: Repository | None = None
) -> ImportResult:
    """Read one StateChange JSON per line and insert into changes."""
    changes, skipped = parse_lines(Path(path).read_text(encoding="utf-8").splitlines())
    shift = align_to_today(changes) if align else 0
    own = repo is None
    if repo is None:
        from app.config import get_settings

        repo = Repository(await init_db(db_path or get_settings().DB_PATH))
    try:
        await repo.add_changes(changes)
    finally:
        if own:
            await repo.close()
    return ImportResult(imported=len(changes), skipped=skipped, shift_days=shift)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import seed history (StateChange JSONL).")
    parser.add_argument("path")
    parser.add_argument("--db", default=None)
    parser.add_argument("--align-to-today", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = asyncio.run(import_seed(args.path, args.db, args.align_to_today))
    print(f"imported {result.imported} changes, skipped {result.skipped} lines"
          + (f", shifted {result.shift_days} days" if result.shift_days else ""))


if __name__ == "__main__":
    main()

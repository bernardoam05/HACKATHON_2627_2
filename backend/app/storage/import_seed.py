"""Seed history importer: python -m app.storage.import_seed demo/seed/history.jsonl

# Owner: Team A
"""
from __future__ import annotations


async def import_seed(path: str):
    """Read one StateChange JSON per line and insert into changes."""
    raise NotImplementedError("Read one StateChange JSON per line and insert into changes.")

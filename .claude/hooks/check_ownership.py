#!/usr/bin/env python3
"""PreToolUse hook: enforce team ownership of files (PLAN.md section 9).

Reads the tool-call JSON from stdin, classifies the target path and prints a
permissionDecision (allow | ask | deny). TEAM comes from the env (A or B), set in the
untracked .claude/settings.local.json: {"env": {"TEAM": "A"}}.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import PurePosixPath

LOCKED = (
    "CLAUDE.md", "PLAN.md", ".claude/", ".gitignore", "backend/pyproject.toml",
    "backend/.env.example", "frontend/package.json", ".github/",
)
SHARED = (
    "backend/app/schemas/", "backend/app/api/", "backend/app/main.py", "backend/app/config.py",
    "frontend/src/api/types.ts", "docs/CONTRACTS.md", "backend/tests/contracts/",
)
TEAM_A = (
    "backend/app/agent/", "backend/app/plugins/", "backend/app/poller/", "backend/app/storage/",
    "backend/app/devices/", "backend/app/mcp_clients/", "backend/tests/team_a/",
)
TEAM_B = (
    "frontend/", "simulator/", "demo/", "backend/fixtures/", "backend/tests/team_b/",
)
BOTH = ("docs/",)


def _match(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p if p.endswith("/") else p + "/") or rel == p
              for p in prefixes)


def normalise(path: str, project_dir: str) -> str | None:
    """Return repo-relative posix path, or None when outside the repo."""
    p = path.replace("\\", "/")
    root = project_dir.replace("\\", "/").rstrip("/")
    if p.lower().startswith(root.lower() + "/"):
        p = p[len(root) + 1:]
    elif p.lower() == root.lower():
        return ""
    elif len(p) > 1 and (p[1] == ":" or p.startswith("/")):
        return None  # absolute path outside the repo
    parts = PurePosixPath(p).parts
    out: list[str] = []
    for part in parts:
        if part == "..":
            if not out:
                return None
            out.pop()
        elif part != ".":
            out.append(part)
    return "/".join(out)


def decide(rel: str | None, team: str | None) -> tuple[str, str]:
    if rel is None:
        return "allow", "Path is outside the repository."
    if team not in ("A", "B"):
        return "deny", ('TEAM is not set. Create the untracked file .claude/settings.local.json with '
                        '{"env": {"TEAM": "A"}} (or "B") and restart Claude Code.')
    if _match(rel, LOCKED):
        return "deny", f"{rel} is locked: change it by hand after team agreement."
    if _match(rel, SHARED):
        return "ask", (f"{rel} is part of the shared contract: needs a contract/* branch and "
                       "cross-team review (update schemas, types.ts, CONTRACTS.md and contract tests together).")
    if _match(rel, TEAM_A):
        return ("allow", "Team A path.") if team == "A" else ("deny", f"{rel} belongs to Team A.")
    if _match(rel, TEAM_B):
        return ("allow", "Team B path.") if team == "B" else ("deny", f"{rel} belongs to Team B.")
    if _match(rel, BOTH):
        return "allow", "Docs are editable by both teams."
    return "ask", f"{rel} has no owner in PLAN.md section 9; confirm with the team."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return 0  # nothing to classify; do not interfere
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    decision, reason = decide(normalise(path, project_dir), os.environ.get("TEAM"))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

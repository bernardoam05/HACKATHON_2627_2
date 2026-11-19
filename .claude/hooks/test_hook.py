"""Run: python -m pytest .claude/hooks/test_hook.py  (from the repo root, or via backend's uv env)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).with_name("check_ownership.py")
ROOT = Path(__file__).resolve().parents[2]


def run(team, rel, tool="Edit"):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)}
    env.pop("TEAM", None)
    if team:
        env["TEAM"] = team
    path = rel if os.path.isabs(rel) or rel.startswith("..") else str(ROOT / rel)
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
        {"tool_name": tool, "tool_input": {"file_path": path}}),
        capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


CASES = [
    # team, path, expected
    ("A", "backend/app/agent/agent.py", "allow"),
    ("B", "backend/app/agent/agent.py", "deny"),
    ("B", "frontend/src/features/chat/ChatPage.tsx", "allow"),
    ("A", "frontend/src/features/chat/ChatPage.tsx", "deny"),
    ("B", "simulator/server.py", "allow"),
    ("A", "simulator/server.py", "deny"),
    ("A", "backend/fixtures/get_home_devices.json", "deny"),
    ("B", "backend/fixtures/get_home_devices.json", "allow"),
    ("A", "backend/app/schemas/device.py", "ask"),
    ("B", "frontend/src/api/types.ts", "ask"),
    ("B", "docs/executive-summary.md", "allow"),
    ("A", "docs/executive-summary.md", "allow"),
    ("A", "docs/CONTRACTS.md", "ask"),
    ("A", "CLAUDE.md", "deny"),
    ("B", ".claude/settings.json", "deny"),
    ("B", "frontend/package.json", "deny"),
    ("A", "backend/pyproject.toml", "deny"),
    ("A", "backend/tests/team_a/test_x.py", "allow"),
    ("B", "backend/tests/team_a/test_x.py", "deny"),
    ("A", "backend/tests/contracts/test_schemas.py", "ask"),
    ("A", "C:/somewhere/else/file.txt", "allow"),
    ("B", "/etc/hosts", "allow"),
    (None, "backend/app/agent/agent.py", "deny"),
    ("C", "backend/app/agent/agent.py", "deny"),
    ("A", "backend\\app\\agent\\agent.py", "allow"),  # Windows separators
    ("B", "backend/app/../app/agent/agent.py", "deny"),  # path traversal normalised
]


@pytest.mark.parametrize("team,rel,expected", CASES)
def test_hook(team, rel, expected):
    assert run(team, rel) == expected


def test_notebook_path_is_checked():
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT), "TEAM": "B"}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
        {"tool_input": {"notebook_path": str(ROOT / "backend/app/agent/n.ipynb")}}),
        capture_output=True, text=True, env=env, check=True).stdout
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

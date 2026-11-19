"""Prompt builders (delta/full) and instructions, including the empty-house rule (D14).

# Owner: Team A
"""
from __future__ import annotations


def build_prompt(run_input) -> str:
    """Render AgentRunInput into the prompt."""
    raise NotImplementedError("Render AgentRunInput into the prompt.")

"""Pydantic AI agent: output_type=Decision, read-only tools only (D10).

# Owner: Team A
"""
from __future__ import annotations


class HomeAgent:
    """run(AgentRunInput) -> Decision; chat(); implements AgentService."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class HomeAgent: run(AgentRunInput) -> Decision; chat(); implements AgentService.")

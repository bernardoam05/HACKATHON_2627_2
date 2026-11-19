"""Read-only agent tools.

# Owner: Team A
"""
from __future__ import annotations


def register_tools(agent):
    """get_home_state, get_device, get_plugin, get_history, get_routine_stats, get_presence_facts. No write tools."""
    raise NotImplementedError("get_home_state, get_device, get_plugin, get_history, get_routine_stats, get_presence_facts. No write tools.")

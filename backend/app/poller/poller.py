"""Poller: reads all enabled devices, diffs, persists, runs reflexes, wakes the agent.

# Owner: Team A
"""
from __future__ import annotations


async def run_poller(services):
    """Loop: sim every SIM_POLL_SECONDS, Altice every ALTICE_POLL_SECONDS. One source failing never stops the other. Run order per PLAN section 3."""
    raise NotImplementedError("Loop: sim every SIM_POLL_SECONDS, Altice every ALTICE_POLL_SECONDS. One source failing never stops the other. Run order per PLAN section 3.")

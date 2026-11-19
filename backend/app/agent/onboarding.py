"""Unknown-device onboarding (D12).

# Owner: Team A
"""
from __future__ import annotations


async def propose_plugin(device) -> object:
    """Return PluginProposal; security-like capabilities must be suggest."""
    raise NotImplementedError("Return PluginProposal; security-like capabilities must be suggest.")

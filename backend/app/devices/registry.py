"""Merged device registry over Altice + sim sources.

# Owner: Team A
"""
from __future__ import annotations


class DeviceRegistry:
    """Merges devices from both clients, applies overrides (alias/room/enabled), read_state() and set_capability(). Implements DeviceService and SimAdminService."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class DeviceRegistry: Merges devices from both clients, applies overrides (alias/room/enabled), read_state() and set_capability(). Implements DeviceService and SimAdminService.")

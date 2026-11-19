"""User feedback and manual-override detection (D9).

# Owner: Team A
"""
from __future__ import annotations


class FeedbackService:
    """Record undo/confirm/reject/manual_override; cooldown per device."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class FeedbackService: Record undo/confirm/reject/manual_override; cooldown per device.")

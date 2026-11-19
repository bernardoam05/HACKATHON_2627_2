"""Plugin registry: builtin YAML + confirmed plugins from storage.

# Owner: Team A
"""
from __future__ import annotations


class PluginRegistry:
    """Resolves autonomy: confirmed plugin -> builtin -> CATEGORY_DEFAULT_AUTONOMY -> suggest. Implements PluginService."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("class PluginRegistry: Resolves autonomy: confirmed plugin -> builtin -> CATEGORY_DEFAULT_AUTONOMY -> suggest. Implements PluginService.")

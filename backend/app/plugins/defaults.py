"""Loads builtin plugins from plugins/types/*.yaml.

# Owner: Team A
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.schemas import DevicePlugin, PluginStatus

TYPES_DIR = Path(__file__).parent / "types"


def load_builtin_plugins(types_dir: Path = TYPES_DIR) -> list[DevicePlugin]:
    """Parse YAML files into DevicePlugin(status=builtin)."""
    plugins: list[DevicePlugin] = []
    for path in sorted(types_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["status"] = PluginStatus.builtin
        plugins.append(DevicePlugin.model_validate(data))
    return plugins

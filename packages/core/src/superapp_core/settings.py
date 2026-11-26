"""The hub's own settings (which plugins are on), kept in one small JSON file."""
from __future__ import annotations

from pathlib import Path

from .paths import read_json, write_json


class Settings:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = read_json(self.path)

    def plugin_enabled(self, plugin_id: str, default: bool) -> bool:
        return bool(self.data.get("plugins", {}).get(plugin_id, default))

    def set_plugin_enabled(self, plugin_id: str, on: bool) -> None:
        self.data.setdefault("plugins", {})[plugin_id] = bool(on)
        write_json(self.path, self.data)

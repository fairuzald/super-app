"""Per-user folders, the right way on each OS (XDG on Linux, AppData on Windows, Library on macOS)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .platform import Platform


@dataclass(frozen=True)
class Paths:
    config: Path
    cache: Path
    data: Path

    @classmethod
    def for_platform(cls, platform: Platform, env: dict[str, str] | None = None, home: Path | None = None) -> Paths:
        env = dict(os.environ) if env is None else env
        home = home or Path.home()
        if platform is Platform.WINDOWS:
            roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
            local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
            return cls(config=roaming, cache=local / "Cache", data=local)
        if platform is Platform.MACOS:
            lib = home / "Library"
            return cls(config=lib / "Application Support", cache=lib / "Caches", data=lib / "Application Support")
        return cls(config=Path(env.get("XDG_CONFIG_HOME") or home / ".config"),
                   cache=Path(env.get("XDG_CACHE_HOME") or home / ".cache"),
                   data=Path(env.get("XDG_DATA_HOME") or home / ".local" / "share"))

    @property
    def user_plugins(self) -> Path:
        """Where people drop add-ons: one folder per plugin."""
        return self.data / "super-app" / "plugins"

    @property
    def settings_file(self) -> Path:
        return self.config / "super-app" / "settings.json"


def read_json(path: os.PathLike | str) -> dict:
    """A JSON object from disk, or {} when it is missing or broken (status files are best-effort)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: os.PathLike | str, data: dict) -> None:
    """Write atomically, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)

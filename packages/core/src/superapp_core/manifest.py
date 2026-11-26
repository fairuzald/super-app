"""plugin.toml: what a plugin is, before any of its code runs."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .platform import Platform

MANIFEST = "plugin.toml"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ENTRY = re.compile(r"^[\w./-]+\.py:[A-Za-z_]\w*$")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    entry: str                       # "plugin.py:ClassName", relative to the plugin folder
    root: Path
    description: str = ""
    platforms: frozenset[Platform] = field(default_factory=lambda: frozenset(Platform))
    default_on: frozenset[Platform] = field(default_factory=lambda: frozenset(Platform))
    color: str = "#22d3ee"
    icon: str = ""
    order: int = 100
    api: int = 1

    @classmethod
    def load(cls, folder: Path) -> PluginManifest:
        path = folder / MANIFEST
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ManifestError(f"{path}: {e}") from e
        return cls.from_dict(raw, folder)

    @classmethod
    def from_dict(cls, raw: dict, root: Path) -> PluginManifest:
        def need(key: str) -> str:
            v = raw.get(key)
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(f"{root.name}: '{key}' is required")
            return v.strip()

        pid, entry = need("id"), need("entry")
        if not _ID.match(pid):
            raise ManifestError(f"{root.name}: id '{pid}' must be lowercase letters, digits and dashes")
        if not _ENTRY.match(entry) or ".." in entry:
            raise ManifestError(f"{pid}: entry must look like 'plugin.py:ClassName'")
        color = raw.get("color", "#22d3ee")
        if not _COLOR.match(str(color)):
            raise ManifestError(f"{pid}: color must be like '#22d3ee'")
        api = int(raw.get("api", 1))
        if api != 1:
            raise ManifestError(f"{pid}: needs plugin API {api}, this Super App has API 1")
        platforms = cls._platforms(raw.get("platforms"), pid)
        default_on = cls._platforms(raw.get("default_on"), pid) if "default_on" in raw else platforms
        return cls(id=pid, name=need("name"), version=str(raw.get("version", "0.0.0")), entry=entry, root=root,
                   description=str(raw.get("description", "")), platforms=platforms, default_on=default_on,
                   color=str(color), icon=str(raw.get("icon", "")), order=int(raw.get("order", 100)), api=api)

    @staticmethod
    def _platforms(value, pid: str) -> frozenset[Platform]:
        if value is None:
            return frozenset(Platform)
        try:
            return frozenset(Platform(v) for v in value)
        except (TypeError, ValueError) as e:
            raise ManifestError(f"{pid}: platforms must be from {[p.value for p in Platform]}") from e

    @property
    def icon_path(self) -> Path | None:
        """The icon file, only if it is inside the plugin folder."""
        if not self.icon:
            return None
        p = (self.root / self.icon).resolve()
        return p if self.root.resolve() in p.parents else None

    def supports(self, platform: Platform) -> bool:
        return platform in self.platforms

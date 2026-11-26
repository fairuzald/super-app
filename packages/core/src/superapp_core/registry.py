"""The set of plugins the hub knows about, and which of them the user has switched on."""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import LoadIssue, PluginDiscovery, PluginFactory
from .manifest import PluginManifest
from .platform import Platform
from .plugin import Plugin
from .settings import Settings


@dataclass
class PluginEntry:
    manifest: PluginManifest
    supported: bool
    enabled: bool
    plugin: Plugin | None = None
    error: str = ""


@dataclass
class PluginRegistry:
    discovery: PluginDiscovery
    factory: PluginFactory
    settings: Settings
    platform: Platform
    entries: list[PluginEntry] = field(default_factory=list)
    issues: list[LoadIssue] = field(default_factory=list)

    def load(self) -> None:
        manifests, self.issues = self.discovery.find()
        self.entries = []
        for m in manifests:
            supported = m.supports(self.platform)
            enabled = supported and self.settings.plugin_enabled(m.id, default=self.platform in m.default_on)
            entry = PluginEntry(m, supported, enabled)
            if enabled:
                self._instantiate(entry)
            self.entries.append(entry)

    def _instantiate(self, entry: PluginEntry) -> None:
        try:
            entry.plugin = self.factory.create(entry.manifest)
            entry.error = ""
        except Exception as e:  # noqa: BLE001 - any add-on bug is reported, not raised
            entry.plugin, entry.error = None, f"{type(e).__name__}: {e}"

    def active(self) -> list[Plugin]:
        return [e.plugin for e in self.entries if e.enabled and e.plugin]

    def get(self, plugin_id: str) -> PluginEntry | None:
        return next((e for e in self.entries if e.manifest.id == plugin_id), None)

    def set_enabled(self, plugin_id: str, on: bool) -> PluginEntry:
        entry = self.get(plugin_id)
        if entry is None:
            raise KeyError(plugin_id)
        if on and not entry.supported:
            raise ValueError(f"{entry.manifest.name} doesn't run on {self.platform.label}")
        self.settings.set_plugin_enabled(plugin_id, on)
        entry.enabled = on
        if on and not entry.plugin:
            self._instantiate(entry)
        return entry

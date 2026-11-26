"""Finds plugin folders and turns them into Plugin objects.

A broken add-on must never take the hub down: every failure becomes a `LoadIssue` the UI can show.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .manifest import MANIFEST, ManifestError, PluginManifest
from .plugin import Plugin, PluginContext


@dataclass(frozen=True)
class LoadIssue:
    where: str
    message: str


class PluginDiscovery:
    """Single job: list valid manifests under some folders. Earlier folders win on duplicate ids."""

    def __init__(self, search_dirs: Iterable[Path]):
        self.search_dirs = [Path(d) for d in search_dirs]

    def find(self) -> tuple[list[PluginManifest], list[LoadIssue]]:
        found: dict[str, PluginManifest] = {}
        issues: list[LoadIssue] = []
        for base in self.search_dirs:
            if not base.is_dir():
                continue
            for folder in sorted(p for p in base.iterdir() if (p / MANIFEST).is_file()):
                try:
                    m = PluginManifest.load(folder)
                except ManifestError as e:
                    issues.append(LoadIssue(str(folder), str(e)))
                    continue
                if m.id in found:
                    issues.append(LoadIssue(str(folder), f"'{m.id}' is already loaded from {found[m.id].root}"))
                    continue
                found[m.id] = m
        return sorted(found.values(), key=lambda m: (m.order, m.name.lower())), issues


class PluginFactory:
    """Single job: import a manifest's entry file and build the plugin with its context."""

    def __init__(self, context: PluginContext):
        self.context = context

    def create(self, manifest: PluginManifest) -> Plugin:
        file, _, cls_name = manifest.entry.partition(":")
        path = (manifest.root / file).resolve()
        if manifest.root.resolve() not in path.parents:
            raise ImportError(f"{manifest.id}: entry is outside the plugin folder")
        mod_name = f"superapp_plugins.{manifest.id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if not spec or not spec.loader:
            raise ImportError(f"{manifest.id}: can't load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(mod_name, None)
            raise
        cls = getattr(module, cls_name, None)
        if not (inspect.isclass(cls) and issubclass(cls, Plugin)) or inspect.isabstract(cls):
            raise TypeError(f"{manifest.id}: {cls_name} must be a concrete subclass of superapp_core.Plugin")
        return cls(manifest, self.context)

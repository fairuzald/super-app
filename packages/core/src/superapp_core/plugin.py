"""The plugin contract.

`Plugin` is the base every add-on extends: it reports a `ToolStatus` for its card and handles its buttons.
`AppPlugin` is the common case of a tool that runs as its own app: it adds "open it, wait, then run the
action" on top, using only the ports it is given - so it works with D-Bus on Linux or anything else.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .manifest import PluginManifest
from .model import QuickAction, ToolStatus
from .paths import Paths
from .platform import Platform
from .ports import AppBridge, Launcher, Notifier, Scheduler


@dataclass(frozen=True)
class PluginContext:
    """Everything a plugin may use. Built by the hub; faked in tests."""
    platform: Platform
    paths: Paths
    bridge: AppBridge
    launcher: Launcher
    scheduler: Scheduler
    notifier: Notifier
    script_runner: tuple[str, ...]   # how to run a tool written as a Python script: (python,) or (exe, "--run-script")


class Plugin(ABC):
    def __init__(self, manifest: PluginManifest, context: PluginContext):
        self.manifest = manifest
        self.ctx = context

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def root(self) -> Path:
        return self.manifest.root

    @abstractmethod
    def status(self) -> ToolStatus:
        """What the card shows now. Called every few seconds on the UI thread: keep it cheap."""

    def quick_actions(self, status: ToolStatus) -> list[QuickAction]:
        return []

    def is_running(self) -> bool:
        return False

    def open(self, action: str | None = None) -> None:  # noqa: B027 - optional hook, default does nothing
        """A card or quick-action button was pressed."""


class AppPlugin(Plugin):
    """A tool that is its own (single-instance) app, started from a script next to the plugin."""

    #: set by subclasses
    app_id: str = ""
    script: str = ""                  # relative to the plugin folder
    start_timeout = 15.0

    @property
    def script_path(self) -> Path:
        return self.root / self.script

    def installed(self) -> bool:
        return self.script_path.exists()

    def is_running(self) -> bool:
        return self.installed() and self.ctx.bridge.is_running(self.app_id)

    def command(self) -> list[str]:
        path = str(self.script_path)
        return [*self.ctx.script_runner, path] if path.endswith(".py") else [path]

    def open(self, action: str | None = None) -> None:
        if not self.installed():
            self.ctx.notifier.notify(f"{self.manifest.name} isn’t installed")
            return
        if self.is_running():
            if action:
                self.ctx.bridge.activate(self.app_id, action)
            else:
                self.ctx.launcher.launch(self.command())       # single instance: brings its window forward
            return
        if not self.ctx.launcher.launch(self.command()):
            self.ctx.notifier.notify(f"Couldn’t open {self.manifest.name}")
            return
        self.ctx.notifier.notify(f"Opening {self.manifest.name}…")
        if action:
            self._when_started(lambda: self.ctx.bridge.activate(self.app_id, action))

    def _when_started(self, then) -> None:
        start = time.monotonic()

        def check() -> bool:
            if self.ctx.bridge.is_running(self.app_id):
                self.ctx.scheduler.call_later(0.4, lambda: (then(), False)[1])
                return False
            return time.monotonic() - start < self.start_timeout
        self.ctx.scheduler.call_later(0.25, check)

"""Small interfaces the hub provides to plugins (dependency inversion: plugins never import the UI).

Each one does one job, so a plugin - or a test - only depends on what it uses.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class AppBridge(Protocol):
    """Talks to a tool that runs as its own app."""

    def is_running(self, app_id: str) -> bool: ...

    def activate(self, app_id: str, action: str) -> None: ...


@runtime_checkable
class Launcher(Protocol):
    def launch(self, argv: Sequence[str]) -> bool: ...


@runtime_checkable
class Scheduler(Protocol):
    """Run `fn` later on the UI thread. `fn` returning True means "call me again"."""

    def call_later(self, seconds: float, fn: Callable[[], bool]) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    def notify(self, message: str) -> None: ...

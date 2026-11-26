"""Super App core: the plugin contract and the services plugins get. No GUI toolkit in here.

The hub (any UI) depends on these abstractions; plugins implement them. Nothing in this package
imports GTK, so it runs - and is tested - the same on Linux, Windows and macOS.
"""
from .manifest import ManifestError, PluginManifest
from .model import Action, Meter, MeterKind, QuickAction, ToolStatus
from .platform import Platform
from .plugin import AppPlugin, Plugin, PluginContext
from .ports import AppBridge, Launcher, Notifier, Scheduler

__all__ = [
    "Action", "AppBridge", "AppPlugin", "Launcher", "ManifestError", "Meter", "MeterKind", "Notifier",
    "Platform", "Plugin", "PluginContext", "PluginManifest", "QuickAction", "Scheduler", "ToolStatus",
]
__version__ = "1.1.0"

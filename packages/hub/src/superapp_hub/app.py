"""Composition root: the one place that picks concrete services and wires them together."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from superapp_core import Platform, PluginContext  # noqa: E402
from superapp_core.loader import PluginDiscovery, PluginFactory  # noqa: E402
from superapp_core.paths import Paths  # noqa: E402
from superapp_core.registry import PluginRegistry  # noqa: E402
from superapp_core.services import ProcessLauncher, script_runner  # noqa: E402
from superapp_core.settings import Settings  # noqa: E402

from .gtk_services import DBusBridge, GLibScheduler, ToastNotifier  # noqa: E402
from .window import HubWindow  # noqa: E402

APP_ID = "local.fairuz.SuperApp"
ASSETS = Path(__file__).resolve().parent / "assets"
ICON = ASSETS / f"{APP_ID}.svg"


def bundled_plugin_dirs() -> list[Path]:
    """Plugins shipped with the app: the repo checkout, a frozen (PyInstaller) bundle, or a system install."""
    here = Path(__file__).resolve()
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "plugins" if getattr(sys, "frozen", False) else None,
        here.parents[4] / "plugins" if len(here.parents) > 4 else None,        # <repo>/packages/hub/src/superapp_hub
        here.parents[2] / "plugins",                                            # <prefix>/lib/superapp_hub (.deb)
        Path(sys.prefix) / "share" / "super-app" / "plugins",
    ]
    return [p for p in candidates if p and p.is_dir()]


def build_registry(platform: Platform, paths: Paths, notifier) -> PluginRegistry:
    context = PluginContext(
        platform=platform, paths=paths,
        bridge=DBusBridge(),            # GApplication actions; without a session bus: "not running"
        launcher=ProcessLauncher(), scheduler=GLibScheduler(), notifier=notifier,
        script_runner=script_runner(platform))
    extra = [Path(p) for p in os.environ.get("SUPERAPP_PLUGINS", "").split(os.pathsep) if p]
    discovery = PluginDiscovery([*extra, paths.user_plugins, *bundled_plugin_dirs()])
    registry = PluginRegistry(discovery, PluginFactory(context), Settings(paths.settings_file), platform)
    registry.load()
    return registry


class HubApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.platform = Platform.current()
        self.paths = Paths.for_platform(self.platform)
        self.notifier = ToastNotifier()
        self.registry: PluginRegistry | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)   # glow cards are dark
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(ASSETS))
        Gtk.Window.set_default_icon_name(APP_ID)
        q = Gio.SimpleAction.new("quit", None)
        q.connect("activate", lambda *_: self.quit())
        self.add_action(q)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.registry = build_registry(self.platform, self.paths, self.notifier)

    def do_activate(self):
        win = self.props.active_window or HubWindow(self, self.registry, self.notifier, ICON,
                                                    self.paths.user_plugins)
        win.present()


def check() -> int:
    """`super-app --check`: load every plugin without opening a window. Used by CI and the installers."""
    platform = Platform.current()
    registry = build_registry(platform, Paths.for_platform(platform), ToastNotifier())
    for e in registry.entries:
        state = "error: " + e.error if e.error else "on" if e.enabled else "off" if e.supported else "unsupported"
        print(f"{e.manifest.id:20} {e.manifest.version:8} {state}")
    for i in registry.issues:
        print(f"NOT LOADED {i.where}: {i.message}")
    ok = registry.entries and not registry.issues and not any(e.error for e in registry.entries)
    return 0 if ok else 1


def run_script(path: str) -> int:
    """`super-app --run-script tool.py`: run a bundled plugin tool with the app's own Python and GTK.

    Only scripts inside a known plugin folder are run, so the installed app can't be used to start
    arbitrary code by path.
    """
    import runpy
    target = Path(path).resolve()
    allowed = [*bundled_plugin_dirs(), Paths.for_platform(Platform.current()).user_plugins]
    if not any(d.resolve() in target.parents for d in allowed if d.is_dir()) or target.suffix != ".py":
        print(f"not a bundled plugin script: {path}", file=sys.stderr)
        return 2
    sys.argv = [str(target)]
    runpy.run_path(str(target), run_name="__main__")
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--run-script":
        return run_script(sys.argv[2])
    if "--check" in sys.argv[1:]:
        return check()
    return HubApplication().run([a for a in sys.argv if a != "--check"])

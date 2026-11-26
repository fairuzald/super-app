"""GTK/GLib implementations of the core ports."""
from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib


class GLibScheduler:
    def call_later(self, seconds: float, fn: Callable[[], bool]) -> None:
        GLib.timeout_add(max(0, int(seconds * 1000)), lambda: bool(fn()))


class ToastNotifier:
    def __init__(self):
        self.overlay: Adw.ToastOverlay | None = None       # set once the window exists

    def notify(self, message: str) -> None:
        if self.overlay:
            self.overlay.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=3))


class DBusBridge:
    """Linux: tools are GApplications; their app id is a bus name, and app actions are org.gtk.Actions."""

    def _bus(self):
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def is_running(self, app_id: str) -> bool:
        try:
            r = self._bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "NameHasOwner", GLib.Variant("(s)", (app_id,)), GLib.VariantType("(b)"),
                                      Gio.DBusCallFlags.NONE, 500, None)
            return r.unpack()[0]
        except GLib.Error:
            return False

    def activate(self, app_id: str, action: str) -> None:
        self._bus().call(app_id, "/" + app_id.replace(".", "/"), "org.gtk.Actions", "Activate",
                         GLib.Variant("(sava{sv})", (action, [], {})), None, Gio.DBusCallFlags.NONE, 3000,
                         None, None, None)

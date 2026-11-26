"""The hub window: a hero, one card per active plugin, and quick actions gathered from all plugins."""
from __future__ import annotations

import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from superapp_core import Plugin
from superapp_core.registry import PluginRegistry

from .plugins_dialog import PluginsDialog
from .theme import build_css
from .widgets import GlowCard, app_icon, pill

REFRESH_SECONDS = 3


def greeting(hour: int | None = None) -> str:
    h = time.localtime().tm_hour if hour is None else hour
    return "Good morning" if 4 <= h < 12 else "Good afternoon" if 12 <= h < 18 else "Good evening"


def wrap_box() -> Gtk.Widget:
    """Adw.WrapBox needs libadwaita 1.7; older systems (e.g. Ubuntu 24.04) get a FlowBox instead."""
    if hasattr(Adw, "WrapBox"):
        return Adw.WrapBox(child_spacing=8, line_spacing=8)
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                       max_children_per_line=12, homogeneous=False)


class HubWindow(Adw.ApplicationWindow):
    def __init__(self, app, registry: PluginRegistry, notifier, icon_path, user_plugins_dir):
        super().__init__(application=app, title="Super App", default_width=680, default_height=600,
                         css_classes=["superapp"])
        self.registry, self.user_plugins_dir = registry, user_plugins_dir
        self.css = Gtk.CssProvider()
        # above the user's theme CSS (~/.config/gtk-4.0/gtk.css), which may restyle every button
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.toasts = Adw.ToastOverlay()
        notifier.overlay = self.toasts

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Plugins", "win.plugins")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        act = Gio.SimpleAction.new("plugins", None)
        act.connect("activate", lambda *_: self.show_plugins())
        self.add_action(act)

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=20, margin_end=20,
                      margin_top=4, margin_bottom=20)
        hero = Gtk.Box(spacing=18, css_classes=["hero"])
        hero.append(app_icon(icon_path, 64))
        hv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        self.hello = Gtk.Label(xalign=0, css_classes=["hero-title"])
        hv.append(self.hello)
        hv.append(Gtk.Label(label="Your tools, all in one place.", xalign=0, css_classes=["hero-sub"]))
        hero.append(hv)
        col.append(hero)

        self.grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        col.append(self.grid)
        self.empty = Gtk.Label(css_classes=["empty"], wrap=True, visible=False,
                               label="No plugins are on. Open Menu › Plugins to switch some on or add new ones.")
        col.append(self.empty)
        col.append(Gtk.Label(label="Quick actions", xalign=0, css_classes=["quick-title"], margin_top=4))
        self.quick = wrap_box()
        col.append(self.quick)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(Adw.Clamp(maximum_size=900, child=col))
        tv.set_content(scroll)
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        self.cards: list[GlowCard] = []
        self._quick_sig = None
        self.rebuild()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self.refresh)

    def rebuild(self) -> None:
        """(Re)create cards for the active plugins - after start-up and when plugins are switched."""
        self.css.load_from_string(build_css([e.manifest for e in self.registry.entries]))
        while (c := self.grid.get_first_child()):
            self.grid.remove(c)
        self.cards = [GlowCard(p) for p in self.registry.active()]
        for n, c in enumerate(self.cards):              # two cards per row
            self.grid.attach(c, n % 2, n // 2, 1, 1)
        self.empty.set_visible(not self.cards)
        self._quick_sig = None
        self.refresh()

    def refresh(self) -> bool:
        self.hello.set_label(greeting())
        statuses = [(c.plugin, c.refresh()) for c in self.cards]
        self.fill_quick(statuses)
        return True

    def fill_quick(self, statuses) -> None:
        items: list[tuple[int, Plugin, object]] = []
        for plugin, st in statuses:
            try:
                items += [(q.priority, plugin, q.action) for q in plugin.quick_actions(st)]
            except Exception:  # noqa: BLE001 - skip a broken plugin's chips
                continue
        items.sort(key=lambda t: t[0])
        sig = tuple((p.id, a) for _, p, a in items)
        if sig == self._quick_sig:
            return
        self._quick_sig = sig
        while (c := self.quick.get_first_child()):
            self.quick.remove(c)
        for _, plugin, action in items:
            b = pill(action, ["quick"])
            b.connect("clicked", lambda *_x, p=plugin, a=action.id: (p.open(a), self._refresh_soon()))
            self.quick.append(b)

    def _refresh_soon(self) -> None:
        GLib.timeout_add(1200, lambda: self.refresh() and False)

    def show_plugins(self) -> None:
        PluginsDialog(self.registry, self.user_plugins_dir, self.rebuild).present(self)

"""Plugins: switch add-ons on/off, see which don't run on this OS or failed to load, open the add-ons folder."""
from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from superapp_core.registry import PluginRegistry
from superapp_core.services import open_in_file_manager

from .widgets import app_icon


class PluginsDialog(Adw.PreferencesDialog):
    def __init__(self, registry: PluginRegistry, user_dir, on_change: Callable[[], None]):
        super().__init__(title="Plugins", search_enabled=False)
        self.registry, self.on_change = registry, on_change
        page = Adw.PreferencesPage()
        self.add(page)

        grp = Adw.PreferencesGroup(title="Installed",
                                   description="Each plugin adds a card and quick actions to the hub.")
        page.add(grp)
        for e in registry.entries:
            m = e.manifest
            if not e.supported:
                note = f"Only for {', '.join(sorted(p.label for p in m.platforms))}"
            elif e.error:
                note = f"Couldn’t load: {e.error}"
            else:
                note = m.description
            row = Adw.SwitchRow(title=m.name, subtitle=GLib.markup_escape_text(f"v{m.version} · {note}"),
                                active=e.enabled, sensitive=e.supported)
            row.add_prefix(app_icon(m.icon_path, 32, "application-x-addon-symbolic"))
            row.connect("notify::active", self._toggled, m.id)
            grp.add(row)

        if registry.issues:
            bad = Adw.PreferencesGroup(title="Not loaded")
            page.add(bad)
            for i in registry.issues:
                bad.add(Adw.ActionRow(title=GLib.markup_escape_text(i.message),
                                      subtitle=GLib.markup_escape_text(i.where)))

        add = Adw.PreferencesGroup(title="Add a plugin",
                                   description="Drop a plugin folder (with its plugin.toml) here, "
                                               "then restart Super App.")
        page.add(add)
        row = Adw.ActionRow(title="Plugins folder", subtitle=GLib.markup_escape_text(str(user_dir)),
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
        row.connect("activated", lambda *_: (user_dir.mkdir(parents=True, exist_ok=True),
                                             open_in_file_manager(str(user_dir))))
        add.add(row)

    def _toggled(self, row, _pspec, plugin_id):
        try:
            entry = self.registry.set_enabled(plugin_id, row.get_active())
        except (KeyError, ValueError) as e:
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(e))))
            return
        if entry.error:
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Couldn’t load: {entry.error}")))
        self.on_change()

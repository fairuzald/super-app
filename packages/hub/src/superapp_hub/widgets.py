"""Reusable hub widgets. `GlowCard` renders any plugin's ToolStatus - it knows nothing about phones or disks."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

from superapp_core import Action, MeterKind, Plugin, ToolStatus

from .theme import css_class


def app_icon(path: Path | None, px: int, fallback: str = "application-x-executable") -> Gtk.Image:
    if path and Path(path).exists():
        return Gtk.Image(gicon=Gio.FileIcon.new(Gio.File.new_for_path(str(path))), pixel_size=px)
    return Gtk.Image(icon_name=fallback, pixel_size=px)


def ring_texture(frac: float, px: int = 72) -> Gdk.Texture:
    """A usage ring drawn as SVG: cyan, then yellow, then red as it fills."""
    color = ("#22d3ee", "#4f8cff") if frac < 0.8 else ("#f8e45c", "#ffa348") if frac < 0.93 else ("#ff7800", "#e01b24")
    r, c = 30, 2 * 3.14159 * 30
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{px * 2}" height="{px * 2}" viewBox="0 0 72 72">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="{color[0]}"/><stop offset="1" stop-color="{color[1]}"/></linearGradient></defs>
      <circle cx="36" cy="36" r="{r}" fill="none" stroke="#ffffff" stroke-opacity="0.1" stroke-width="8"/>
      <circle cx="36" cy="36" r="{r}" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"
              stroke-dasharray="{c * frac:.1f} {c:.1f}" transform="rotate(-90 36 36)"/></svg>"""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(svg.encode())
    loader.close()
    return Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())


def pill(action: Action, css: list[str]) -> Gtk.Button:
    box = Gtk.Box(spacing=8)
    box.append(Gtk.Image(icon_name=action.icon))
    box.append(Gtk.Label(label=action.label))
    return Gtk.Button(child=box, css_classes=css)


class GlowCard(Gtk.Box):
    """Header (icon, name, subtitle, running dot), one big headline, an optional meter, action buttons."""

    def __init__(self, plugin: Plugin):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True,
                         css_classes=["glow", css_class(plugin.manifest)])
        self.plugin = plugin
        self.status: ToolStatus | None = None
        self._actions_sig = None
        self._ring_frac = None

        head = Gtk.Box(spacing=12)
        head.append(app_icon(plugin.manifest.icon_path, 44))
        tv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        tv.append(Gtk.Label(label=plugin.manifest.name, xalign=0, css_classes=["card-title"]))
        self.sub = Gtk.Label(xalign=0, css_classes=["card-sub"], ellipsize=3)
        tv.append(self.sub)
        head.append(tv)
        self.dot = Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER)
        head.append(self.dot)
        hb = Gtk.Button(child=head, css_classes=["flat", "head"], tooltip_text=plugin.manifest.description)
        hb.connect("clicked", lambda *_: plugin.open(None))
        self.append(hb)

        mid = Gtk.Box(spacing=12, vexpand=True)
        sv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.headline = Gtk.Label(xalign=0, css_classes=["stat"])
        self.caption = Gtk.Label(xalign=0, css_classes=["stat-label"], wrap=True)
        sv.append(self.headline)
        sv.append(self.caption)
        self.bar = Gtk.ProgressBar(css_classes=["batt"], margin_top=10, visible=False)
        sv.append(self.bar)
        mid.append(sv)
        self.ring = Gtk.Image(pixel_size=76)
        self.ring_label = Gtk.Label(css_classes=["ring-label"])
        self.ring_box = Gtk.Overlay(child=self.ring, visible=False, valign=Gtk.Align.CENTER)
        self.ring_box.add_overlay(self.ring_label)
        mid.append(self.ring_box)
        self.append(mid)
        self.acts = Gtk.Box(spacing=8)
        self.append(self.acts)

    def refresh(self) -> ToolStatus:
        try:
            st = self.plugin.status()
            running = self.plugin.is_running()
        except Exception as e:  # noqa: BLE001 - a plugin bug shows on its card, the hub keeps going
            st, running = ToolStatus("Plugin error", "—", f"{type(e).__name__}: {e}"), False
        self.status = st
        self.sub.set_label(st.subtitle)
        (self.dot.add_css_class if running else self.dot.remove_css_class)("on")
        self.dot.set_tooltip_text("Running" if running else "Not open")
        self.headline.set_label(st.headline)
        self.caption.set_label(st.caption)
        m = st.meter
        self.bar.set_visible(bool(m and m.kind is MeterKind.BAR))
        self.ring_box.set_visible(bool(m and m.kind is MeterKind.RING))
        if m and m.kind is MeterKind.BAR:
            self.bar.set_fraction(m.value)
            (self.bar.add_css_class if m.warn else self.bar.remove_css_class)("low")
        if m and m.kind is MeterKind.RING and round(m.value, 2) != self._ring_frac:
            self._ring_frac = round(m.value, 2)
            self.ring.set_from_paintable(ring_texture(m.value))
            self.ring_label.set_label(f"{m.value * 100:.0f}%")
            self.ring_box.set_tooltip_text(m.tooltip or None)
        if st.actions != self._actions_sig:                 # rebuild buttons only when they change
            self._actions_sig = st.actions
            while (c := self.acts.get_first_child()):
                self.acts.remove(c)
            for n, a in enumerate(st.actions):
                lead = [f"lead-{css_class(self.plugin.manifest)}"] if n == 0 else []
                b = pill(a, ["pill"] + lead)
                b.connect("clicked", lambda *_x, aid=a.id: self.plugin.open(aid))
                self.acts.append(b)
        return st

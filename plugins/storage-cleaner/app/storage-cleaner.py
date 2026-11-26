#!/usr/bin/python3
"""Storage Cleaner - see what fills your disk and free space safely (GTK4 + libadwaita)."""
import json, os, queue, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleaner_core as core
from cleaner_core import fmt_size, short

APP_ID = "local.fairuz.StorageCleaner"
SUMMARY = os.path.join(core.CACHE, "storage-cleaner", "last-scan.json")   # read by the Super App hub
VERSION = "1.0"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
CFG = os.path.join(core.CONFIG, "storage-cleaner.json")

CSS = """
.tile { padding: 0; border-radius: 18px; transition: background 150ms ease, box-shadow 150ms ease; }
.tile:hover { background-color: alpha(@accent_bg_color, 0.10); }
.tile-icon { border-radius: 12px; min-width: 42px; min-height: 42px; color: white; }
.tile-icon.small { border-radius: 10px; min-width: 36px; min-height: 36px; }
.c-blue   { background: linear-gradient(135deg, #62a0ea, #1c71d8); }
.c-red    { background: linear-gradient(135deg, #f66151, #c01c28); }
.c-green  { background: linear-gradient(135deg, #57e389, #26a269); }
.c-teal   { background: linear-gradient(135deg, #4fd2d9, #1a8a94); }
.c-orange { background: linear-gradient(135deg, #ffbe6f, #e66100); }
.c-yellow { background: linear-gradient(135deg, #f8e45c, #e5a50a); }
.c-pink   { background: linear-gradient(135deg, #f387c8, #c2338a); }
.c-slate  { background: linear-gradient(135deg, #9a9996, #5e5c64); }
.badge { border-radius: 999px; padding: 1px 8px; font-size: 0.85em; font-weight: bold;
         background: alpha(@accent_bg_color, 0.18); color: @accent_color; }
.badge.zero { background: alpha(currentColor, 0.08); color: alpha(currentColor, 0.6); }
.tile-sub { font-size: 0.85em; font-weight: bold; color: @success_color; }
.tile-sub.dim-label { color: inherit; font-weight: normal; }
.section { font-weight: bold; opacity: 0.75; }
.section-icon { opacity: 0.6; }
.hero { padding: 18px; border-radius: 18px; }
.hero-size { font-size: 2.1em; font-weight: 800; }
.disk { padding: 16px; border-radius: 18px; }
.disk-bar trough, .disk-bar progress { min-height: 10px; border-radius: 999px; }
.disk-bar.warn progress { background: @warning_color; }
.disk-bar.full progress { background: @error_color; }
.size-bar trough, .size-bar progress { min-height: 6px; border-radius: 999px; }
.chip { border-radius: 999px; padding: 4px 10px; font-size: 0.9em; font-weight: bold; }
.chip.safe    { background: alpha(@success_color, 0.16); color: @success_color; }
.chip.review  { background: alpha(@warning_color, 0.18); color: @warning_color; }
.chip.careful { background: alpha(@error_color, 0.16); color: @error_color; }
.thumb { border-radius: 8px; background: alpha(currentColor, 0.07); }
.size-label { font-feature-settings: "tnum"; font-weight: bold; }
.bottom { padding: 10px 14px; }
.busy-pill { border-radius: 999px; padding: 8px 16px; background: alpha(#0d1017, 0.96); color: white;
             font-weight: bold; box-shadow: inset 0 0 0 1px alpha(#4f8cff, 0.55), 0 4px 20px alpha(black, 0.5); }

/* ---- Dark galaxy theme (shared look with Super App): near-black, thin neon #4f8cff accents, no purple ---- */
:root { --accent-bg-color: #4f8cff; --accent-fg-color: #05060a; --accent-color: #4f8cff;
        --window-bg-color: #05060a; --view-bg-color: #0a0c12; --headerbar-bg-color: transparent;
        --headerbar-shade-color: transparent; --card-bg-color: #0d1017; --dialog-bg-color: #0d1017;
        --popover-bg-color: #0d1017; --sidebar-bg-color: #0a0c12; --success-color: #5eead4; }
window.background { background-color: #05060a;
  background-image: radial-gradient(circle, alpha(white, 0.35) 0.7px, transparent 1.4px),
                    radial-gradient(circle, alpha(#7dd3fc, 0.25) 0.7px, transparent 1.4px),
                    radial-gradient(ellipse at 10% 0%, alpha(#1e3a8a, 0.16), transparent 50%),
                    radial-gradient(ellipse at 100% 100%, alpha(#0e7490, 0.10), transparent 50%);
  background-size: 137px 149px, 211px 197px, 100% 100%, 100% 100%;
  background-position: 0 0, 60px 90px, 0 0, 0 0;
  background-repeat: repeat, repeat, no-repeat, no-repeat; }
.card { background-color: alpha(#0d1017, 0.94); border-radius: 20px; box-shadow: inset 0 0 0 1px alpha(white, 0.06); }
.tile { border-radius: 20px; }
.tile:hover { background-color: #11151e; box-shadow: inset 0 0 0 1px alpha(#4f8cff, 0.40), 0 0 16px alpha(#4f8cff, 0.08); }
.hero, .glow { border-radius: 22px; background-color: alpha(#0d1017, 0.95);
               background-image: radial-gradient(circle at 95% 0%, alpha(#4f8cff, 0.10), transparent 55%);
               box-shadow: inset 0 0 0 1px alpha(#4f8cff, 0.22); }
button.pill { border-radius: 999px; padding: 8px 18px; font-weight: bold; }
button.suggested-action { background: alpha(#4f8cff, 0.12); color: #4f8cff;
                          box-shadow: inset 0 0 0 1px alpha(#4f8cff, 0.55); }
button.suggested-action:hover { background: alpha(#4f8cff, 0.20); box-shadow: inset 0 0 0 1px #4f8cff, 0 0 14px alpha(#4f8cff, 0.25); }
button.suggested-action:disabled { background: alpha(white, 0.04); color: alpha(white, 0.35); box-shadow: inset 0 0 0 1px alpha(white, 0.08); }
progressbar progress { background: linear-gradient(90deg, #4f8cff, #22d3ee); border-radius: 999px; }
progressbar trough { background: alpha(white, 0.06); border-radius: 999px; }
.badge { background: alpha(#4f8cff, 0.10); color: #4f8cff; }
.tile-sub { color: #4f8cff; }
.boxed-list, list.boxed-list { background-color: alpha(#0d1017, 0.94); border-radius: 18px;
                               box-shadow: inset 0 0 0 1px alpha(white, 0.06); }
.hero-size { font-size: 2.6em; font-weight: 800; color: white; }
"""

SAFETY = {
    "safe": ("emblem-ok-symbolic", "Safe to clean", "These come back by themselves when needed."),
    "review": ("dialog-information-symbolic", "Check before cleaning", "Some of these may be your own files."),
    "careful": ("dialog-warning-symbolic", "Be careful", "Removed for good; this can’t be undone."),
}
METHOD = {
    "rebuild": "You choose: move to the Trash (can be restored) or delete right away.",
    "files": "Moved to the Trash, so you can still restore them.",
    "permanent": "Removed for good when you confirm.",
}
SECTIONS = [
    ("Quick wins", "starred-symbolic", ["caches", "devcaches", "trash"]),
    ("Your projects", "folder-symbolic", ["node", "build", "docker"]),
    ("Your files", "folder-documents-symbolic", ["downloads", "large", "explore"]),
    ("System", "emblem-system-symbolic", ["system"]),
]


DOWNLOAD_DAYS = (30, 90, 180, 365)
LARGE_MB = (100, 250, 500, 1000, 5000)


def load_cfg():
    """Settings file; anything unexpected in it falls back to the defaults."""
    try:
        with open(CFG) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(cfg, dict):
        return {}
    if cfg.get("downloads_days") not in DOWNLOAD_DAYS:
        cfg.pop("downloads_days", None)
    if cfg.get("large_min_mb") not in LARGE_MB:
        cfg.pop("large_min_mb", None)
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        with open(CFG, "w") as f:
            json.dump(cfg, f, indent=1)
    except OSError:
        pass


def show_in_files(path, win):
    f = Gio.File.new_for_path(path)
    launcher = Gtk.FileLauncher.new(f)

    def done(l, res):
        try:
            l.open_containing_folder_finish(res)
        except GLib.Error:
            pass
    launcher.open_containing_folder(win, None, done)


def open_folder(path, win):
    def done(l, res):
        try:
            l.launch_finish(res)
        except GLib.Error:
            pass
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(win, None, done)


def icon_square(icon, color, small=False):
    return Gtk.Image(icon_name=icon, pixel_size=18 if small else 22,
                     css_classes=["tile-icon", color] + (["small"] if small else []), valign=Gtk.Align.CENTER)


# ---------------------------------------------------------------- thumbnails (background loader)
class Thumbs:
    q = queue.Queue()
    started = False
    IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml", "image/tiff")

    @classmethod
    def want(cls, box, path):
        if not cls.started:
            cls.started = True
            for _ in range(2):
                threading.Thread(target=cls.worker, daemon=True).start()
        cls.q.put((box, path))

    @classmethod
    def worker(cls):
        while True:
            box, path = cls.q.get()
            gicon, tex = None, None
            try:
                info = Gio.File.new_for_path(path).query_info(
                    "thumbnail::path,standard::icon,standard::content-type,standard::size",
                    Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
                gicon = info.get_icon()
                tp = info.get_attribute_byte_string("thumbnail::path")
                if tp and os.path.exists(tp):
                    tex = Gdk.Texture.new_from_filename(tp)
                elif info.get_content_type() in cls.IMAGE_TYPES and info.get_size() < 40 << 20:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 80, 80, True)
                    tex = Gdk.Texture.new_for_pixbuf(pb)
            except Exception:
                pass
            GLib.idle_add(cls.apply, box, gicon, tex)

    @staticmethod
    def apply(box, gicon, tex):
        child = box.get_first_child()
        if tex:
            if child:
                box.remove(child)
            box.append(Gtk.Picture(paintable=tex, content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                   width_request=40, height_request=40))
        elif gicon and child:
            child.set_from_gicon(gicon)
        return False


def thumb_widget(path):
    box = Gtk.Box(css_classes=["thumb"], width_request=40, height_request=40, valign=Gtk.Align.CENTER,
                  halign=Gtk.Align.START, hexpand=False, overflow=Gtk.Overflow.HIDDEN)
    box.append(Gtk.Image(icon_name="text-x-generic-symbolic", pixel_size=22, width_request=40, opacity=0.7))
    Thumbs.want(box, path)
    return box


# ---------------------------------------------------------------- tile
class Tile(Gtk.Button):
    """A feature card: coloured icon, title, caption, size badge and a short status line."""

    def __init__(self, icon, color, title, caption, cb):
        super().__init__(css_classes=["card", "tile"], hexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        top = Gtk.Box(spacing=6, margin_bottom=2)
        top.append(icon_square(icon, color))
        top.append(Gtk.Box(hexpand=True))
        self.spinner = Adw.Spinner(visible=False, valign=Gtk.Align.START, width_request=16, height_request=16)
        top.append(self.spinner)
        self.badge = Gtk.Label(css_classes=["badge"], valign=Gtk.Align.START, visible=False)
        top.append(self.badge)
        box.append(top)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"], wrap=True))
        box.append(Gtk.Label(label=caption, xalign=0, wrap=True, lines=2, css_classes=["caption", "dim-label"],
                             ellipsize=3))
        self.sub = Gtk.Label(xalign=0, css_classes=["tile-sub"], visible=False, wrap=True)
        box.append(self.sub)
        self.set_child(box)
        self.connect("clicked", lambda *_: cb())

    def set_state(self, scanning=False, badge=None, sub=None, zero=False, neutral=False):
        self.spinner.set_visible(scanning)
        self.badge.set_visible(bool(badge) and not scanning)
        self.badge.set_label(badge or "")
        (self.badge.add_css_class if zero else self.badge.remove_css_class)("zero")
        self.sub.set_visible(bool(sub) and not scanning)
        self.sub.set_label(sub or "")
        (self.sub.add_css_class if neutral else self.sub.remove_css_class)("dim-label")


# ---------------------------------------------------------------- category / quick-clean page
class CleanPage(Adw.NavigationPage):
    """List of items with checkboxes and a Clean button. Shows one category, or the
    recommended items of all categories (quick=True)."""

    def __init__(self, win, cat=None, quick=False):
        title = "Recommended cleanup" if quick else cat.title
        super().__init__(title=title, tag=("quick" if quick else cat.id))
        self.win, self.cat, self.quick, self.busy = win, cat, quick, False
        self.entries = []
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan again")
        self.refresh_btn.connect("clicked", lambda *_: self.rescan())
        hb.pack_end(self.refresh_btn)
        tv.add_top_bar(hb)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=680, margin_start=16, margin_end=16, margin_top=12, margin_bottom=24)
        scroll.set_child(clamp)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(self.col)
        self.col.append(self.intro())
        opts = self.options()
        if opts:
            self.col.append(opts)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        load = Adw.StatusPage(title="Looking…", description="This runs in the background, so you can keep using the app.")
        load.set_paintable(None)
        load.set_child(Adw.Spinner(width_request=32, height_request=32))
        self.stack.add_named(load, "loading")
        self.empty = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="Nothing to clean here",
                                    description="All tidy.")
        self.empty_btn = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"], visible=False)
        self.empty_btn.connect("clicked", lambda *_: self.on_notice_action())
        self.empty.set_child(self.empty_btn)
        self.stack.add_named(self.empty, "empty")
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.stack.add_named(self.list_box, "list")
        self.col.append(self.stack)
        tv.set_content(scroll)

        bar = Gtk.Box(spacing=12, css_classes=["bottom"])
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.sel_label = Gtk.Label(xalign=0, css_classes=["heading"])
        self.sel_hint = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"], wrap=True)
        self.progress = Gtk.ProgressBar(visible=False, margin_top=4)
        v.append(self.sel_label)
        v.append(self.sel_hint)
        v.append(self.progress)
        bar.append(v)
        self.clean_btn = Gtk.Button(css_classes=["pill", "suggested-action"], valign=Gtk.Align.CENTER)
        self.clean_btn.connect("clicked", lambda *_: self.confirm())
        bar.append(self.clean_btn)
        tv.add_bottom_bar(bar)
        self.set_child(tv)
        self.reload()

    # ---- header parts
    def intro(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card", "hero"])
        top = Gtk.Box(spacing=12)
        if self.quick:
            top.append(icon_square("starred-symbolic", "c-green"))
            what = ("Everything ticked here is safe and comes back by itself when an app needs it. "
                    "Untick anything you want to keep, then press Clean up. Your Trash and personal files "
                    "that need a closer look are left out.")
            safety, method = "safe", "rebuild"
        else:
            top.append(icon_square(self.cat.icon, self.cat.color))
            what, safety, method = self.cat.what, self.cat.safety, self.cat.method
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t.append(Gtk.Label(label="What is this?", xalign=0, css_classes=["heading"]))
        t.append(Gtk.Label(label=what, xalign=0, wrap=True, css_classes=["body"]))
        top.append(t)
        card.append(top)
        icon, label, desc = SAFETY[safety]
        row = Gtk.Box(spacing=8)
        chip = Gtk.Box(spacing=6, css_classes=["chip", safety], valign=Gtk.Align.CENTER)
        chip.append(Gtk.Image(icon_name=icon, pixel_size=14))
        chip.append(Gtk.Label(label=label))
        row.append(chip)
        row.append(Gtk.Label(label=desc, xalign=0, wrap=True, hexpand=True, css_classes=["caption", "dim-label"]))
        card.append(row)
        m = Gtk.Box(spacing=8)
        m.append(Gtk.Image(icon_name="dialog-password-symbolic" if (self.cat and self.cat.needs_root)
                           else "user-trash-symbolic", pixel_size=14, css_classes=["dim-label"]))
        text = METHOD[method]
        if self.cat and self.cat.needs_root:
            text += " You’ll be asked for your password."
        m.append(Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True, css_classes=["caption", "dim-label"]))
        card.append(m)
        return card

    def options(self):
        if self.quick or self.cat.id not in ("downloads", "large"):
            return None
        grp = Adw.PreferencesGroup()
        if self.cat.id == "downloads":
            choices = list(zip(DOWNLOAD_DAYS, ("1 month", "3 months", "6 months", "1 year")))
            cur = self.cat.days
            row = Adw.ComboRow(title="Show downloads older than",
                               subtitle="Duplicates, unfinished downloads and installed installers are always shown")
        else:
            choices = list(zip(LARGE_MB, ("100 MB", "250 MB", "500 MB", "1 GB", "5 GB")))
            cur = self.cat.min_mb
            row = Adw.ComboRow(title="Show files bigger than")
        row.set_model(Gtk.StringList.new([c[1] for c in choices]))
        row.set_selected(next((i for i, c in enumerate(choices) if c[0] == cur), 1))

        def changed(r, _p):
            val = choices[r.get_selected()][0]
            if self.cat.id == "downloads":
                self.cat.days = self.win.cfg["downloads_days"] = val
            else:
                self.cat.min_mb = self.win.cfg["large_min_mb"] = val
            save_cfg(self.win.cfg)
            self.rescan()
        row.connect("notify::selected", changed)
        grp.add(row)
        return grp

    # ---- content
    def gather(self):
        if not self.quick:
            return [(self.cat, i) for i in self.cat.items]
        out = []
        for cid in self.win.order:
            c = self.win.cats[cid]
            if cid in ("trash", "large"):
                continue
            out += [(c, i) for i in c.items if i.checked]
        return out

    def scanning(self):
        if self.quick:
            return bool(self.win.pending - {"folders"})
        return self.cat.id in self.win.pending

    def reload(self):
        if self.busy:
            return
        self.entries = self.gather()
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()
        if self.scanning() and not self.entries:
            self.stack.set_visible_child_name("loading")
        elif not self.entries:
            notice = self.cat.notice if self.cat else None
            if self.cat and self.cat.error:
                notice = ("Couldn’t check this", self.cat.error, None)
            title, desc, action = notice or ("Nothing to clean here", "All tidy. 🎉", None)
            self.empty.set_title(title)
            self.empty.set_description(desc)
            self.empty.set_icon_name("emblem-ok-symbolic" if not notice else "dialog-information-symbolic")
            self.empty_btn.set_visible(action == "start")
            self.empty_btn.set_label("Start Docker")
            self.stack.set_visible_child_name("empty")
        else:
            self.build_groups()
            self.stack.set_visible_child_name("list")
        self.refresh_btn.set_sensitive(not self.scanning())
        self.update_summary()

    def build_groups(self):
        groups = {}
        for c, it in self.entries:
            key = c.title if self.quick else (it.group or "Found")
            groups.setdefault(key, []).append((c, it))
        if self.quick:
            order = [self.win.cats[cid].title for cid in self.win.order]
        else:
            order = list(self.cat.group_order)
        for key in sorted(groups, key=lambda k: order.index(k) if k in order else 99):
            ents = groups[key]
            total = sum(i.size for _c, i in ents)
            grp = Adw.PreferencesGroup(title=GLib.markup_escape_text(key),
                                       description=f"{len(ents)} item{'s' if len(ents) != 1 else ''}  ·  {fmt_size(total)}")
            checks = []
            for c, it in ents[:400]:
                grp.add(self.row(c, it, checks))
            if len(ents) > 400:
                grp.add(Adw.ActionRow(title=f"…and {len(ents) - 400} smaller items", sensitive=False))
            if len(ents) > 1:
                b = Gtk.Button(label="Select all", css_classes=["flat"], valign=Gtk.Align.CENTER)

                def toggle(btn, cs=checks):
                    target = not all(c.get_active() for c in cs)
                    for c in cs:
                        c.set_active(target)
                    btn.set_label("Select none" if target else "Select all")
                b.set_label("Select none" if all(i.checked for _c, i in ents) else "Select all")
                b.connect("clicked", toggle)
                grp.set_header_suffix(b)
            self.list_box.append(grp)

    def row(self, cat, it, checks):
        row = Adw.ActionRow(title=it.title, use_markup=False, subtitle_lines=3, title_lines=2)
        sub = it.subtitle
        if it.note:
            sub = f"{sub}\n{it.note}" if sub else it.note
        if it.warn:
            sub = f"{sub}\n⚠ {it.warn}"
        row.set_subtitle(sub)
        chk = Gtk.CheckButton(active=it.checked, valign=Gtk.Align.CENTER)

        def toggled(c, item=it):
            item.checked = c.get_active()
            self.update_summary()
        chk.connect("toggled", toggled)
        checks.append(chk)
        if it.thumb and it.paths:               # add_prefix puts each new prefix first: thumbnail, then checkbox
            row.add_prefix(thumb_widget(it.paths[0]))
        row.add_prefix(chk)
        row.set_activatable_widget(chk)
        row.add_suffix(Gtk.Label(label=fmt_size(it.size), css_classes=["size-label"], valign=Gtk.Align.CENTER))
        if it.kind == "path" and it.paths and os.path.lexists(it.paths[0]):
            b = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Show in Files", valign=Gtk.Align.CENTER,
                           css_classes=["flat"])
            b.connect("clicked", lambda *_a, p=it.paths[0]: show_in_files(p, self.win))
            row.add_suffix(b)
        return row

    def selected(self):
        return [(c, i) for c, i in self.entries if i.checked]

    def update_summary(self):
        if self.busy:
            return
        sel = self.selected()
        size = sum(i.size for _c, i in sel)
        if not self.entries:
            self.sel_label.set_label("Nothing selected")
        else:
            self.sel_label.set_label(f"{len(sel)} of {len(self.entries)} selected  ·  {fmt_size(size)}")
        needs_pw = any(i.kind == "root" for _c, i in sel)
        self.sel_hint.set_label("You’ll be asked for your password" if needs_pw else
                                ("Nothing is removed until you confirm" if sel else "Tick what you want to remove"))
        self.clean_btn.set_label(f"Clean up {fmt_size(size)}" if sel else "Clean up")
        self.clean_btn.set_sensitive(bool(sel))
        permanent = sel and all(i.kind != "path" for _c, i in sel)
        for cls, on in (("destructive-action", permanent), ("suggested-action", not permanent)):
            (self.clean_btn.add_css_class if on else self.clean_btn.remove_css_class)(cls)

    # ---- actions
    def rescan(self):
        cats = [self.win.cats[c] for c in self.win.order if c not in ("trash", "large")] if self.quick else [self.cat]
        for c in cats:
            self.win.scan_category(c)
        self.reload()

    def on_notice_action(self):
        if self.cat and self.cat.id == "docker":
            self.empty_btn.set_sensitive(False)
            self.empty_btn.set_label("Starting…")

            def work():
                r = core.start_docker()
                GLib.idle_add(after, r)

            def after(r):
                self.empty_btn.set_sensitive(True)
                if r.returncode:
                    self.win.toast("Docker didn’t start" + (" (password cancelled)" if r.returncode in (126, 127) else ""))
                self.rescan()
            threading.Thread(target=work, daemon=True).start()

    def confirm(self):
        sel = self.selected()
        if not sel:
            return
        size = sum(i.size for _c, i in sel)
        paths = [(c, i) for c, i in sel if i.kind == "path"]
        rebuild = any(c.method == "rebuild" for c, _i in paths)
        lines = [f"•  {i.title}  —  {fmt_size(i.size)}" for _c, i in sorted(sel, key=lambda e: -e[1].size)[:8]]
        if len(sel) > 8:
            lines.append(f"…and {len(sel) - 8} more")
        body = "\n".join(lines) + "\n\n"
        if paths and rebuild:
            body += ("“Move to Trash” lets you restore them later; “Delete now” frees the space right away. "
                     "Caches and packages are recreated when needed.")
            if any(c.method == "files" for c, _i in paths):
                body += " Your own files always go to the Trash."
        elif paths:
            body += "They will be moved to the Trash, so you can still restore them."
        if any(i.kind != "path" for _c, i in sel):
            body += ("\n\n" if paths else "") + "Docker, system and Trash items are removed for good."
        if any(i.kind == "root" for _c, i in sel):
            body += " You’ll be asked for your password."
        warns = [i.warn for _c, i in sel if i.warn]
        if warns:
            body += "\n\n⚠ " + "\n⚠ ".join(dict.fromkeys(warns))
        d = Adw.AlertDialog(heading=f"Clean up {fmt_size(size)}?", body=body)
        d.add_response("cancel", "Cancel")
        if paths and rebuild:
            d.add_response("delete", "Delete now")
            d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            d.add_response("trash", "Move to Trash")
            d.set_response_appearance("trash", Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("trash")
        elif paths:
            d.add_response("trash", "Move to Trash")
            d.set_response_appearance("trash", Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("trash")
        else:
            d.add_response("clean", "Remove for good")
            d.set_response_appearance("clean", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response("cancel")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r != "cancel" and self.run_clean(sel, r))
        d.present(self.win)

    def run_clean(self, sel, response):
        self.busy = True
        self.busy_done = self.win.busy(f"Cleaning {len(sel)} item{'s' if len(sel) != 1 else ''}…")
        sp = Gtk.Box(spacing=8)
        sp.append(Adw.Spinner(width_request=16, height_request=16))
        sp.append(Gtk.Label(label="Cleaning…"))
        self.clean_btn.set_child(sp)
        self.clean_btn.set_sensitive(False)
        self.refresh_btn.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        delete = response == "delete"
        # "Delete now" only applies to rebuildable things; your own files always go to the Trash.
        hard = [i for c, i in sel if i.kind == "path" and delete and c.method == "rebuild"]
        soft = [i for c, i in sel if i.kind == "path" and not (delete and c.method == "rebuild")]
        other = [i for _c, i in sel if i.kind != "path"]
        steps = len(sel) or 1

        def prog(offset):
            def f(n, _total, title):
                GLib.idle_add(self.set_progress, (offset + n) / steps, title)
            return f

        def work():
            res = {"freed": 0, "trashed": 0, "errors": [], "trashed_paths": []}
            off = 0
            for items, mode in ((hard, "delete"), (soft, "trash"), (other, "delete")):
                if items:
                    r = core.clean_items(items, mode, prog(off))
                    off += len(items)
                    for k in res:
                        res[k] += r[k]
            GLib.idle_add(self.done, sel, res)
        threading.Thread(target=work, daemon=True).start()

    def set_progress(self, frac, title):
        self.progress.set_fraction(min(1, frac))
        self.sel_label.set_label("Cleaning…")
        self.sel_hint.set_label(title)
        self.busy_done.update(f"Cleaning… {min(100, int(frac * 100))}%")
        return False

    def done(self, sel, res):
        self.busy = False
        self.busy_done()
        self.progress.set_visible(False)
        failed = {t for t, _m in res["errors"]}
        for c, i in sel:                 # drop what is gone right away; the rescan confirms it
            if i.title not in failed and i in c.items:
                c.items.remove(i)
        self.win.after_clean({c for c, _i in sel}, res)
        self.win.show_result(res)
        self.reload()
        return False


# ---------------------------------------------------------------- folder explorer
class ExplorerPage(Adw.NavigationPage):
    def __init__(self, win, path):
        name = "Home folder" if path == core.HOME else os.path.basename(path)
        super().__init__(title=name, tag=f"explore:{path}")
        self.win, self.path = win, path
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        ob = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open in Files")
        ob.connect("clicked", lambda *_: open_folder(path, win))
        hb.pack_end(ob)
        tv.add_top_bar(hb)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=680, margin_start=16, margin_end=16, margin_top=12, margin_bottom=24)
        scroll.set_child(clamp)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(self.col)
        self.stack = Gtk.Stack(vhomogeneous=False)
        load = Adw.StatusPage(title="Measuring folders…",
                              description="The first look at your whole home folder takes a minute.")
        load.set_child(Adw.Spinner(width_request=32, height_request=32))
        self.stack.add_named(load, "loading")
        self.group = Adw.PreferencesGroup()
        self.stack.add_named(self.group, "list")
        self.stack.add_named(Adw.StatusPage(icon_name="folder-symbolic", title="This folder is empty"), "empty")
        hint = Gtk.Label(label=("Biggest first. Open a folder to look inside. The bin button moves it to the Trash. "
                                "Folders with a dot (like .cache) hold app data."),
                         wrap=True, xalign=0, css_classes=["caption", "dim-label"])
        self.col.append(hint)
        self.col.append(self.stack)
        tv.set_content(scroll)
        self.set_child(tv)
        self.rows = []
        self.load()

    def load(self):
        if not self.win.folders.ready:
            self.stack.set_visible_child_name("loading")
            self.win.folder_waiters.append(self.load)
            return
        self.stack.set_visible_child_name("loading")

        def work():
            kids = self.win.folders.children(self.path)
            GLib.idle_add(self.show, kids)
        threading.Thread(target=work, daemon=True).start()

    def show(self, kids):
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        if not kids:
            self.stack.set_visible_child_name("empty")
            return False
        total = sum(k[3] for k in kids) or 1
        biggest = kids[0][3] or 1
        self.group.set_title(f"{fmt_size(total)} in {len(kids)} items")
        for name, p, is_dir, size in kids[:200]:
            self.add_row(name, p, is_dir, size, total, biggest)
        if len(kids) > 200:
            rest = kids[200:]
            r = Adw.ActionRow(title=f"…and {len(rest)} smaller items ({fmt_size(sum(k[3] for k in rest))})",
                              sensitive=False)
            self.group.add(r)
            self.rows.append(r)
        self.stack.set_visible_child_name("list")
        return False

    def add_row(self, name, p, is_dir, size, total, biggest):
        row = Adw.ActionRow(title=name, use_markup=False,
                            subtitle=f"{fmt_size(size)}  ·  {size * 100 / total:.0f}% of this folder")
        if is_dir:
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic", pixel_size=24))
        else:
            ctype, _u = Gio.content_type_guess(name, None)
            row.add_prefix(Gtk.Image(gicon=Gio.content_type_get_symbolic_icon(ctype), pixel_size=24))
        bar = Gtk.ProgressBar(fraction=size / biggest, valign=Gtk.Align.CENTER, width_request=90,
                              css_classes=["size-bar"])
        row.add_suffix(bar)
        if core.is_safe(p):
            b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Move to Trash", valign=Gtk.Align.CENTER,
                           css_classes=["flat"])
            b.connect("clicked", lambda *_: self.trash(row, name, p, size, b))
            row.add_suffix(b)
        else:       # same-size slot keeps the bars aligned and says why there is no bin
            row.add_suffix(Gtk.Button(icon_name="changes-prevent-symbolic", valign=Gtk.Align.CENTER,
                                      css_classes=["flat"], sensitive=False,
                                      tooltip_text="Protected: Storage Cleaner never removes this"))
        if is_dir:
            row.set_activatable(True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_: self.win.nav.push(ExplorerPage(self.win, p)))
        else:
            b = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Show in Files", valign=Gtk.Align.CENTER,
                           css_classes=["flat"])
            b.connect("clicked", lambda *_: show_in_files(p, self.win))
            row.add_suffix(b)
        self.group.add(row)
        self.rows.append(row)

    def trash(self, row, name, p, size, btn=None):
        rel = os.path.relpath(p, core.HOME)
        app = any(part.startswith(".") for part in rel.split(os.sep))
        body = f"It uses {fmt_size(size)}. You can restore it from the Trash."
        if app:
            body += ("\n\n⚠ This is inside an app’s data or settings folder. Removing it can reset or break that app. "
                     "Prefer the cleanup categories for caches.")
        d = Adw.AlertDialog(heading=f"Move “{name}” to the Trash?", body=body)
        d.add_response("cancel", "Cancel")
        d.add_response("trash", "Move to Trash")
        d.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE if app else Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def resp(_d, r):
            if r != "trash":
                return
            row.set_sensitive(False)
            old_sub = row.get_subtitle()
            row.set_subtitle("Moving to the Trash…")
            spin = Adw.Spinner(width_request=16, height_request=16, valign=Gtk.Align.CENTER)
            if btn:
                btn.set_child(spin)
            done = self.win.busy(f"Moving “{name}” to the Trash…")

            def work():
                try:
                    core.remove_path(p, "trash")
                    err = None
                except Exception as e:
                    err = e.message if isinstance(e, GLib.Error) else str(e)
                GLib.idle_add(after, err)

            def after(err):
                done()
                if err:
                    row.set_sensitive(True)
                    row.set_subtitle(old_sub)
                    if btn:
                        btn.set_child(None)
                        btn.set_icon_name("user-trash-symbolic")
                    self.win.toast(f"Couldn’t move it: {err}")
                else:
                    self.group.remove(row)
                    self.rows.remove(row)
                    self.win.folders.forget(p, size)
                    self.win.after_clean(set(), {"freed": 0, "trashed": size, "errors": [], "trashed_paths": [p]},
                                         quiet=True)
                    self.win.toast(f"Moved “{name}” to the Trash ({fmt_size(size)})", "Open Trash",
                                   lambda: self.win.open_category("trash"))
                return False
            threading.Thread(target=work, daemon=True).start()
        d.connect("response", resp)
        d.present(self.win)


# ---------------------------------------------------------------- main window
class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Storage Cleaner", default_width=620, default_height=900)
        self.cfg = load_cfg()
        self.cats = {C.id: C() for C in core.CATEGORIES}
        self.cats["downloads"].days = self.cfg.get("downloads_days", 90)
        self.cats["large"].min_mb = self.cfg.get("large_min_mb", 500)
        self.order = [C.id for C in core.CATEGORIES]
        self.pending = set()
        self.freed_session = 0
        self.folders = core.FolderSizes()
        self.folder_waiters = []
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.tiles = {}

        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)   # above ~/.config/gtk-4.0/gtk.css
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.nav.connect("popped", lambda *_: GLib.idle_add(self.refresh_pages))
        over = Gtk.Overlay(child=self.nav)
        self.busy_label = Gtk.Label()
        pillbox = Gtk.Box(spacing=10, css_classes=["busy-pill"])
        pillbox.append(Adw.Spinner(width_request=18, height_request=18))
        pillbox.append(self.busy_label)
        self.busy_rev = Gtk.Revealer(child=pillbox, halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=58,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, can_target=False)
        over.add_overlay(self.busy_rev)
        self.busy_jobs = []
        self.toasts.set_child(over)
        self.set_content(self.toasts)
        self.nav.add(self.build_home())
        for name, fn in (("rescan", self.scan_all), ("about", self.about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=fn: f())
            self.add_action(act)
        self.update_disk()
        GLib.idle_add(self.scan_all)

    # ---- home page
    def build_home(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.wtitle = Adw.WindowTitle(title="Storage Cleaner")
        hb.set_title_widget(self.wtitle)
        self.scan_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan again")
        self.scan_btn.connect("clicked", lambda *_: self.scan_all())
        menu = Gio.Menu()
        menu.append("Scan again", "win.rescan")
        menu.append("About Storage Cleaner", "win.about")
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        hb.pack_end(self.scan_btn)
        tv.add_top_bar(hb)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=680, margin_start=16, margin_end=16, margin_bottom=24)
        scroll.set_child(clamp)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12)
        clamp.set_child(col)

        # disk card
        disk = Gtk.Box(spacing=14, css_classes=["card", "disk"])
        disk.append(icon_square("drive-harddisk-symbolic", "c-slate"))
        dv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.disk_title = Gtk.Label(xalign=0, css_classes=["heading"])
        self.disk_bar = Gtk.ProgressBar(css_classes=["disk-bar"])
        self.disk_sub = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"], wrap=True)
        dv.append(self.disk_title)
        dv.append(self.disk_bar)
        dv.append(self.disk_sub)
        disk.append(dv)
        col.append(disk)

        # hero card
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card", "hero"])
        self.hero_pre = Gtk.Label(label="You can free up to", xalign=0, css_classes=["dim-label"])
        self.hero_size = Gtk.Label(label="…", xalign=0, css_classes=["hero-size"])
        self.hero_desc = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"],
                                   label="Only things that are safe and come back by themselves are counted. "
                                         "Nothing is removed until you press Clean up and confirm.")
        self.scan_bar = Gtk.ProgressBar(margin_top=6)
        self.scan_label = Gtk.Label(xalign=0, css_classes=["caption"], wrap=True)
        self.quick_btn = Gtk.Button(css_classes=["pill", "suggested-action"], halign=Gtk.Align.START, margin_top=8)
        self.quick_btn.set_child(Adw.ButtonContent(icon_name="edit-clear-all-symbolic", label="Review & clean up"))
        self.quick_btn.connect("clicked", lambda *_: self.nav.push(CleanPage(self, quick=True)))
        for w in (self.hero_pre, self.hero_size, self.hero_desc, self.scan_bar, self.scan_label, self.quick_btn):
            hero.append(w)
        col.append(hero)

        # tiles
        for title, icon, ids in SECTIONS:
            ids = [i for i in ids if i == "explore" or i in self.cats]     # some categories are Linux-only
            if not ids:
                continue
            head = Gtk.Box(spacing=8, margin_top=10)
            head.append(Gtk.Image(icon_name=icon, pixel_size=16, css_classes=["section-icon"]))
            head.append(Gtk.Label(label=title, xalign=0, css_classes=["section"]))
            col.append(head)
            grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
            for n, cid in enumerate(ids):
                if cid == "explore":
                    t = Tile("folder-saved-search-symbolic", "c-slate", "Explore folders",
                             "See what takes space and remove anything", lambda: self.open_explorer(core.HOME))
                else:
                    c = self.cats[cid]
                    t = Tile(c.icon, c.color, c.title, c.caption, lambda cid=cid: self.open_category(cid))
                self.tiles[cid] = t
                grid.attach(t, n % 3, n // 3, 1, 1)
            col.append(grid)

        # biggest folders
        self.big_group = Adw.PreferencesGroup(title="Biggest folders in your home", margin_top=10,
                                              description="Measuring…")
        eb = Gtk.Button(label="Explore", css_classes=["flat"], valign=Gtk.Align.CENTER)
        eb.connect("clicked", lambda *_: self.open_explorer(core.HOME))
        self.big_group.set_header_suffix(eb)
        self.big_rows = []
        col.append(self.big_group)
        tv.set_content(scroll)
        return Adw.NavigationPage(title="Storage Cleaner", child=tv, tag="home")

    def update_disk(self):
        root = core.disk_usage(core.system_drive())
        home = core.disk_usage(core.HOME)
        if not root:
            return False
        frac = root["used"] / root["total"] if root["total"] else 0
        self.disk_title.set_label(f"{fmt_size(root['used'])} of {fmt_size(root['total'])} used")
        self.disk_bar.set_fraction(frac)
        for cls, on in (("warn", 0.8 <= frac < 0.93), ("full", frac >= 0.93)):
            (self.disk_bar.add_css_class if on else self.disk_bar.remove_css_class)(cls)
        sub = f"{fmt_size(root['free'])} free on this computer"
        if home and home["dev"] != root["dev"]:
            sub += f"  ·  Home drive: {fmt_size(home['free'])} free of {fmt_size(home['total'])}"
        hs = self.folders.sizes.get(core.HOME)
        if hs:
            sub += f"  ·  Your home folder uses {fmt_size(hs)}"
        self.disk_sub.set_label(sub)
        return False

    # ---- scanning
    def scan_all(self):
        if self.pending:
            return
        core.ProjectIndex.found = None
        for cid in self.order:
            self.scan_category(self.cats[cid])
        self.pending.add("folders")
        self.tiles["explore"].set_state(scanning=True)
        self.pool.submit(self.scan_folders)
        self.update_scan_status()

    def scan_category(self, cat):
        if cat.id in self.pending:
            return
        if cat.id == "node":              # node and build share one walk; node always starts a fresh one
            core.ProjectIndex.found = None
        self.pending.add(cat.id)
        self.tiles[cat.id].set_state(scanning=True)
        self.update_scan_status()

        def work():
            cat.refresh()
            GLib.idle_add(self.category_done, cat)
        self.pool.submit(work)

    def scan_folders(self):
        self.folders.scan()
        GLib.idle_add(self.folders_done)

    def folders_done(self):
        self.pending.discard("folders")
        self.update_scan_status()
        kids = [k for k in self.folders.children(core.HOME) if k[2]][:6]
        for r in self.big_rows:
            self.big_group.remove(r)
        self.big_rows = []
        total = self.folders.sizes.get(core.HOME, 0)
        self.big_group.set_description(f"Your home folder uses {fmt_size(total)}. Tap a folder to look inside.")
        biggest = kids[0][3] if kids else 1
        for name, p, _d, size in kids:
            row = Adw.ActionRow(title=name, use_markup=False, subtitle=fmt_size(size), activatable=True)
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic", pixel_size=24))
            row.add_suffix(Gtk.ProgressBar(fraction=size / (biggest or 1), valign=Gtk.Align.CENTER, width_request=110,
                                           css_classes=["size-bar"]))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_a, p=p: self.open_explorer(p))
            self.big_group.add(row)
            self.big_rows.append(row)
        self.tiles["explore"].set_state(badge=fmt_size(total) if total else None, zero=True)
        self.update_disk()
        waiters, self.folder_waiters = self.folder_waiters, []
        for w in waiters:
            w()
        return False

    def category_done(self, cat):
        self.pending.discard(cat.id)
        t = self.tiles[cat.id]
        if cat.notice:
            t.set_state(badge=cat.notice[0].replace("Docker ", "").capitalize(), zero=True)
        elif cat.error:
            t.set_state(badge="Error", zero=True)
        elif not cat.items:
            t.set_state(badge="Clean", zero=True)
        else:
            rec = cat.recommended
            t.set_state(badge=fmt_size(cat.total),
                        sub=(f"{fmt_size(rec)} ticked for cleanup" if rec else "You pick what to remove"),
                        neutral=not rec)
        self.update_scan_status()
        self.refresh_pages(cat)
        return False

    def update_scan_status(self):
        total = len(self.order) + 1
        busy = len(self.pending)
        rec = sum(c.recommended for cid, c in self.cats.items() if cid not in ("trash", "large"))
        self.hero_size.set_label(fmt_size(rec) if rec or not busy else "…")
        self.scan_btn.set_sensitive(not busy)
        self.scan_bar.set_visible(bool(busy))
        self.scan_bar.set_fraction((total - busy) / total)
        names = [self.cats[c].title if c in self.cats else "folder sizes" for c in self.order + ["folders"]
                 if c in self.pending]
        self.scan_label.set_visible(bool(busy))
        self.scan_label.set_label("Checking " + ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
                                  if names else "")
        self.quick_btn.set_sensitive(bool(rec))
        self.hero_pre.set_label("Found so far" if busy else "You can free up to")
        if not busy:
            self.save_summary(rec)

    def save_summary(self, rec):
        """Last scan result, so the Super App hub can show it without scanning the disk itself."""
        trash = self.cats.get("trash")
        data = {"freeable": rec, "trash": trash.total if trash else 0, "time": time.time()}
        try:
            os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
            with open(SUMMARY + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(SUMMARY + ".tmp", SUMMARY)
        except OSError:
            pass

    def refresh_pages(self, cat=None):
        page = self.nav.get_visible_page()
        if isinstance(page, CleanPage) and (cat is None or page.quick or page.cat is cat):
            page.reload()
        return False

    # ---- navigation
    def open_category(self, cid):
        self.nav.push(CleanPage(self, self.cats[cid]))

    def open_explorer(self, path):
        self.nav.push(ExplorerPage(self, path))

    # ---- after cleaning
    def after_clean(self, cats, res, quiet=False):
        self.freed_session += res["freed"]
        if self.freed_session:
            self.wtitle.set_subtitle(f"Freed {fmt_size(self.freed_session)} this session")
        for p in res.get("trashed_paths", []):
            self.folders.forget(p, 0)
        GLib.timeout_add(300, self.update_disk)
        todo = {c.id for c in cats}
        if res["trashed"] or "trash" in todo or quiet:
            todo.add("trash")
        if todo & {"node", "build"}:
            core.ProjectIndex.found = None
            todo |= {"node", "build"} & set(self.order)
        for cid in self.order:
            if cid in todo:
                self.scan_category(self.cats[cid])

    def show_result(self, res):
        freed, trashed, errors = res["freed"], res["trashed"], res["errors"]
        if freed and trashed:
            head = f"Freed {fmt_size(freed)}, and {fmt_size(trashed)} moved to the Trash"
        elif trashed:
            head = f"Moved {fmt_size(trashed)} to the Trash"
        elif freed:
            head = f"Freed {fmt_size(freed)} 🎉"
        else:
            head = "Nothing was removed"
        body = ""
        if trashed:
            body = ("The space comes back when those items leave the Trash. Delete just these items from the "
                    "Trash now? (Anything else in your Trash stays.)")
        if errors:
            body += ("\n\n" if body else "") + f"{len(errors)} item{'s' if len(errors) > 1 else ''} couldn’t be cleaned:\n"
            body += "\n".join(f"•  {t}: {m}" for t, m in errors[:6])
        d = Adw.AlertDialog(heading=head, body=body)
        if trashed:
            d.add_response("keep", "Keep in Trash")
            d.add_response("empty", f"Delete for good ({fmt_size(trashed)})")
            d.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_close_response("keep")
        else:
            d.add_response("ok", "OK")
        paths = list(res.get("trashed_paths", []))

        def resp(_d, r):
            if r != "empty":
                return

            busy_done = self.busy("Deleting from the Trash…")

            def work():
                n = core.delete_from_trash(paths)
                GLib.idle_add(after, n)

            def after(n):
                busy_done()
                self.after_clean({self.cats["trash"]}, {"freed": n, "trashed": 0, "errors": []})
                self.toast(f"Freed {fmt_size(n)}")
                return False
            threading.Thread(target=work, daemon=True).start()
        d.connect("response", resp)
        d.present(self)
        if freed and not trashed:
            self.toast(f"Freed {fmt_size(freed)}")

    def busy(self, msg):
        """Show a floating spinner with msg until the returned function is called."""
        job = [msg]
        self.busy_jobs.append(job)
        self._show_busy()

        def done():
            if job in self.busy_jobs:
                self.busy_jobs.remove(job)
            self._show_busy()

        def update(text):
            job[0] = text
            self._show_busy()
        done.update = update
        return done

    def _show_busy(self):
        if self.busy_jobs:
            self.busy_label.set_label(self.busy_jobs[-1][0])
        self.busy_rev.set_reveal_child(bool(self.busy_jobs))

    def toast(self, msg, button=None, cb=None):
        t = Adw.Toast(title=GLib.markup_escape_text(msg[:180]), timeout=5)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: cb())
        self.toasts.add_toast(t)

    def about(self):
        Adw.AboutDialog(application_name="Storage Cleaner", application_icon=APP_ID, version=VERSION,
                        comments="See what fills your disk and free space safely.",
                        license_type=Gtk.License.MIT_X11).present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)   # glow theme is dark
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ASSETS)
        Gtk.Window.set_default_icon_name(APP_ID)
        q = Gio.SimpleAction.new("quit", None)
        q.connect("activate", lambda *_: self.quit())
        self.add_action(q)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.rescan", ["<Control>r", "F5"])
        # Actions the Super App hub can start directly (over D-Bus, org.gtk.Actions)
        for name, fn in (("clean-up", lambda w: w.nav.push(CleanPage(w, quick=True))),
                         ("explore", lambda w: w.open_explorer(core.HOME)),
                         ("trash", lambda w: w.open_category("trash"))):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=fn: self.hub_action(f))
            self.add_action(act)

    def hub_action(self, fn):
        w = self.props.active_window or Win(self)
        w.present()
        w.nav.pop_to_tag("home")
        fn(w)

    def do_activate(self):
        (self.props.active_window or Win(self)).present()


if __name__ == "__main__":
    App().run(None)

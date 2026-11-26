#!/usr/bin/python3
"""Phone Connect - pair, connect and mirror an Android phone over Wi-Fi (adb + scrcpy).

Discovery uses the system avahi daemon (D-Bus), pairing uses Android's
"Pair device with QR code", mirroring uses scrcpy.
"""
import json, os, re, secrets, shlex, shutil, signal, string, subprocess, tempfile, threading, time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import companion  # noqa: E402  (tablet control panel / monitor / drawing pad)
import display  # noqa: E402  (tablet as an extra monitor)

APP_ID = "local.fairuz.PhoneConnect"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
CFG = os.path.expanduser("~/.config/phone-connect.json")
# Small live summary (phone, battery) that the Super App hub reads while this app runs
STATUS = os.path.join(GLib.get_user_cache_dir(), "phone-connect", "status.json")


def write_status(state):
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATUS)
    except OSError:
        pass
_SDK_ADB = os.path.expanduser("~/Android/Sdk/platform-tools/adb")
# Prefer Google's SDK adb: the older distro adb drops Wi-Fi debugging connections.
ADB = _SDK_ADB if os.access(_SDK_ADB, os.X_OK) else (shutil.which("adb") or _SDK_ADB)
# adb must not reconnect the phone on its own (it would survive "disconnect" and app close)
os.environ["ADB_MDNS_AUTO_CONNECT"] = "0"
KEEP_ON_S = 4 * 60 * 60      # phone never sleeps while mirroring (scrcpy restores the timeout itself)
ADDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}:\d{2,5}$")
CONNECT_T, PAIR_T = "_adb-tls-connect._tcp", "_adb-tls-pairing._tcp"
HOWTO = ("On your phone: Settings › About phone › tap <b>Build number</b> 7 times. "
         "Then Settings › System › <b>Developer options</b> › turn on <b>Wireless debugging</b>. "
         "Keep the phone on the same Wi-Fi as this laptop.")


# ---------------------------------------------------------------- helpers
def load_cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        with open(CFG, "w") as f:
            json.dump(cfg, f)
    except Exception:
        pass


def adb(*args, timeout=15):
    try:
        p = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return "Timed out"
    except FileNotFoundError:
        return "adb not found"


def phone_key(service_name):
    """adb-<serial>-<random> -> adb-<serial> (stable per phone)."""
    parts = service_name.split("-")
    return "-".join(parts[:2]) if len(parts) >= 3 and parts[0] == "adb" else service_name


def connect_ok(out):
    return out.startswith("connected to") or out.startswith("already connected")


def list_devices(raw=False):
    devs = []
    for line in adb("devices", "-l", timeout=6).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "Phone")
            devs.append({"serial": parts[0], "state": parts[1], "model": model.replace("_", " "),
                         "wifi": ":" in parts[0] or "._tcp" in parts[0]})
    # adb may also auto-connect the same phone by its mDNS name: keep the IP:port entry only
    if raw:
        return devs
    ip_models = {d["model"] for d in devs if ADDR_RE.match(d["serial"])}
    return [d for d in devs if not (d["serial"].endswith("._tcp") and d["model"] in ip_models)]


def bg(fn, done=None):
    def run():
        res = fn()
        if done:
            GLib.idle_add(done, res)
    threading.Thread(target=run, daemon=True).start()


class Avahi:
    """Minimal mDNS browser through avahi-daemon's D-Bus API."""

    def __init__(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
            self.srv = Gio.DBusProxy.new_sync(self.bus, 0, None, "org.freedesktop.Avahi", "/",
                                              "org.freedesktop.Avahi.Server2", None)
            self.srv.call_sync("GetVersionString", None, 0, 3000, None)
            self.ok = True
        except Exception:
            self.ok = False

    def browse(self, typ, on_new, on_remove=None):
        """Calls on_new(name, ip, port) / on_remove(name). Returns a handle for stop()."""
        if not self.ok:
            return None
        path = self.srv.call_sync("ServiceBrowserPrepare",
                                  GLib.Variant("(iissu)", (-1, 0, typ, "local", 0)), 0, -1, None)[0]

        def on_sig(_c, _s, _p, _i, signal, params, _u):
            if signal not in ("ItemNew", "ItemRemove"):
                return
            iface, proto, name, styp, dom, _f = params.unpack()
            if signal == "ItemRemove" and on_remove:
                on_remove(name)
            elif signal == "ItemNew":
                def resolved(src, res):
                    try:
                        r = src.call_finish(res).unpack()
                        on_new(name, r[7], int(r[8]))
                    except Exception:
                        pass
                self.srv.call("ResolveService", GLib.Variant(
                    "(iisssiu)", (iface, proto, name, styp, dom, 0, 0)), 0, 8000, None, resolved)

        sub = self.bus.signal_subscribe("org.freedesktop.Avahi", "org.freedesktop.Avahi.ServiceBrowser",
                                        None, path, None, 0, on_sig, None)
        self.bus.call_sync("org.freedesktop.Avahi", path, "org.freedesktop.Avahi.ServiceBrowser",
                           "Start", None, None, 0, -1, None)
        return (path, sub)

    def stop(self, handle):
        if not handle:
            return
        path, sub = handle
        self.bus.signal_unsubscribe(sub)
        try:
            self.bus.call_sync("org.freedesktop.Avahi", path, "org.freedesktop.Avahi.ServiceBrowser",
                               "Free", None, None, 0, -1, None)
        except Exception:
            pass


# ---------------------------------------------------------------- pair dialog
def steps_label(lines):
    return Gtk.Label(wrap=True, xalign=0, use_markup=True,
                     label="\n".join(f"<b>{i}.</b>  {t}" for i, t in enumerate(lines, 1)))


class PairDialog(Adw.Dialog):
    """Three ways to pair: QR code, 6-digit code, or USB cable."""

    def __init__(self, win, tab="qr"):
        super().__init__(title="Pair a phone", content_width=440, content_height=660)
        self.win = win
        self.name = "studio-" + secrets.token_hex(4)
        self.password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        self.busy = False
        self.done = False

        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.views = Adw.ViewStack(vexpand=True)
        switcher = Adw.InlineViewSwitcher(stack=self.views, margin_start=16, margin_end=16,
                                          margin_bottom=8, display_mode=Adw.InlineViewSwitcherDisplayMode.BOTH)
        tv.add_top_bar(switcher)
        self.views.add_titled_with_icon(self.qr_page(), "qr", "QR code", "qr-code-symbolic")
        self.views.add_titled_with_icon(self.code_page(), "code", "Pairing code", "input-dialpad-symbolic")
        self.views.add_titled_with_icon(self.usb_page(), "usb", "USB cable", "drive-removable-media-symbolic")
        tv.set_content(self.views)

        # shared status line
        st = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=16,
                     margin_start=16, margin_end=16)
        self.spinner = Adw.Spinner()
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        st.append(self.spinner)
        st.append(self.status)
        tv.add_bottom_bar(st)
        self.set_child(tv)
        self.views.connect("notify::visible-child-name", lambda *_: self.tab_changed())
        self.views.set_visible_child_name(tab)
        self.tab_changed()

        self.browser = win.avahi.browse(PAIR_T, self.on_pair_service)
        self.connect("closed", lambda *_: win.avahi.stop(self.browser))

    # ---- pages
    def page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=8, margin_bottom=8, margin_start=24, margin_end=24)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(box)
        return scroll, box

    def qr_page(self):
        scroll, box = self.page()
        box.append(steps_label(["On the phone open <b>Developer options › Wireless debugging</b>",
                                "Tap <b>Pair device with QR code</b>",
                                "Point the phone camera at this code"]))
        frame = Gtk.Box(halign=Gtk.Align.CENTER, css_classes=["card"])
        frame.append(self.make_qr(f"WIFI:T:ADB;S:{self.name};P:{self.password};;"))
        box.append(frame)
        return scroll

    def code_page(self):
        scroll, box = self.page()
        box.append(steps_label(["On the phone open <b>Developer options › Wireless debugging</b>",
                                "Tap <b>Pair device with pairing code</b>",
                                "Type the 6-digit code below (the address fills in by itself)"]))
        grp = Adw.PreferencesGroup()
        self.code_addr = Adw.EntryRow(title="IP address and port", use_markup=False)
        self.code = Adw.EntryRow(title="6-digit code", input_purpose=Gtk.InputPurpose.DIGITS)
        self.code.connect("entry-activated", self.on_code_pair)
        grp.add(self.code_addr)
        grp.add(self.code)
        box.append(grp)
        btn = Gtk.Button(label="Pair", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        btn.connect("clicked", self.on_code_pair)
        box.append(btn)
        return scroll

    def usb_page(self):
        scroll, box = self.page()
        box.append(steps_label(["Connect the phone to this laptop with a USB cable",
                                "On the phone turn on <b>Developer options › USB debugging</b>",
                                "Press the button below and tap <b>Allow</b> on the phone",
                                "Unplug the cable — the phone stays connected over Wi-Fi"]))
        box.append(Gtk.Label(label="Works until the phone restarts. Good when QR pairing fails.",
                             css_classes=["caption", "dim-label"], wrap=True, xalign=0))
        btn = Gtk.Button(label="Connect over USB", halign=Gtk.Align.CENTER,
                         css_classes=["pill", "suggested-action"])
        btn.connect("clicked", self.on_usb)
        box.append(btn)
        return scroll

    def tab_changed(self):
        if self.busy or self.done:
            return
        tab = self.views.get_visible_child_name()
        self.set_status({"qr": "Waiting for your phone to scan…",
                         "code": "Waiting for the pairing code…",
                         "usb": "Plug in the phone to start"}[tab], spinning=tab == "qr")

    def make_qr(self, text):
        pic = Gtk.Picture(can_shrink=False, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        try:
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            subprocess.run(["qrencode", "-t", "PNG", "-s", "8", "-m", "2", "-o", path, text],
                           check=True, timeout=5)
            pic.set_filename(path)
            pic.set_size_request(240, 240)
            GLib.timeout_add_seconds(2, lambda: (os.unlink(path), False)[1])
        except Exception:
            return Gtk.Label(label="Could not draw the QR code.\nUse the Pairing code tab instead.",
                             margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        return pic

    def set_status(self, text, spinning=True):
        self.status.set_label(text)
        self.spinner.set_visible(spinning)

    # ---- actions
    def on_pair_service(self, name, ip, port):
        if name == self.name and not self.busy and not self.done:
            self.views.set_visible_child_name("qr")
            self.pair(f"{ip}:{port}", self.password)
        elif name != self.name and not self.code_addr.get_text():
            self.code_addr.set_text(f"{ip}:{port}")  # phone is showing the pairing-code screen
            if self.views.get_visible_child_name() == "qr" and not self.busy:
                self.views.set_visible_child_name("code")

    def on_code_pair(self, *_):
        addr, code = self.code_addr.get_text().strip(), self.code.get_text().strip()
        if not ADDR_RE.match(addr) or not code:
            self.set_status("Enter the IP address and port and the code shown on the phone", spinning=False)
            return
        self.pair(addr, code)

    def pair(self, addr, secret):
        self.busy = True
        self.set_status("Pairing…")

        def done(out):
            self.busy = False
            if "Successfully paired" in out:
                self.finish("Paired! Connecting…")
            else:
                self.set_status("Pairing failed: " + (out.splitlines()[-1] if out else "unknown error")
                                + ". Try again.", spinning=False)
        bg(lambda: adb("pair", addr, secret, timeout=25), done)

    def on_usb(self, *_):
        if self.busy:
            return
        self.busy = True
        self.set_status("Looking for a phone on USB…")

        def work():
            usb = [d for d in list_devices() if not d["wifi"]]
            if not usb:
                return False, "No phone on USB. Check the cable and that USB debugging is on."
            d = usb[0]
            if d["state"] == "unauthorized":
                return False, "Tap “Allow” on the phone, then press the button again."
            if d["state"] != "device":
                return False, f"Phone is {d['state']}. Unplug and plug it in again."
            s = d["serial"]
            out = adb("-s", s, "shell", "ip", "-f", "inet", "addr", "show", "wlan0", timeout=8)
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            if not m:
                return False, "The phone is not on Wi-Fi. Connect it to the same Wi-Fi as this laptop."
            ip = m.group(1)
            serialno = adb("-s", s, "get-serialno", timeout=5).strip()
            adb("-s", s, "tcpip", "5555", timeout=10)
            for _ in range(8):
                time.sleep(1)
                if connect_ok(adb("connect", f"{ip}:5555", timeout=6)):
                    return True, (f"adb-{serialno}", f"{ip}:5555", d["model"])
            return False, "Could not reach the phone over Wi-Fi. Are both on the same network?"

        def done(res):
            self.busy = False
            ok, info = res
            if ok:
                key, addr, model = info
                self.win.remember_phone(key, model, tcp=addr)
                self.finish("Connected! You can unplug the cable.")
            else:
                self.set_status(info, spinning=False)
        bg(work, done)

    def finish(self, text):
        self.done = True
        self.set_status(text)
        self.win.after_pair()
        GLib.timeout_add(1500, lambda: (self.close(), False)[1])


# ---------------------------------------------------------------- styling
CSS = """
.tile { padding: 0; border-radius: 18px; }
.tile.active { box-shadow: inset 0 0 0 2px @accent_color; }
.tile-icon { border-radius: 12px; min-width: 42px; min-height: 42px; color: white; }
.c-blue   { background: linear-gradient(135deg, #62a0ea, #1c71d8); }
.c-red    { background: linear-gradient(135deg, #f66151, #c01c28); }
.c-green  { background: linear-gradient(135deg, #57e389, #26a269); }
.c-teal   { background: linear-gradient(135deg, #4fd2d9, #1a8a94); }
.c-orange { background: linear-gradient(135deg, #ffbe6f, #e66100); }
.c-purple { background: linear-gradient(135deg, #c061cb, #813d9c); }
.c-yellow { background: linear-gradient(135deg, #f8e45c, #e5a50a); }
.c-pink   { background: linear-gradient(135deg, #f387c8, #c2338a); }
.badge { border-radius: 999px; padding: 1px 8px; font-size: 0.8em; font-weight: bold;
         background: alpha(@accent_bg_color, 0.18); color: @accent_color; }
.badge.live { background: #e01b24; color: white; }
.phone-mock { border-radius: 26px; min-width: 104px; min-height: 184px; border: 4px solid alpha(currentColor, 0.85);
              background: linear-gradient(160deg, #33c7de, #1a5fb4); color: white; }
.phone-mock .notch { background: alpha(currentColor, 0.85); border-radius: 999px; min-width: 34px; min-height: 6px; }
.section { font-weight: bold; opacity: 0.75; }
.thumb { border-radius: 8px; background: alpha(currentColor, 0.08); }
.tile { transition: background 150ms ease, box-shadow 150ms ease; }
.tile:hover { background-color: alpha(@accent_bg_color, 0.10); }
.chip { border-radius: 999px; padding: 4px 10px; background: alpha(currentColor, 0.08); font-size: 0.9em; }
.mock-time { font-size: 30px; font-weight: 300; }
.mock-date { font-size: 11px; opacity: 0.85; }
.section-icon { opacity: 0.6; }
.find-title { font-size: 1.6em; font-weight: 800; }
.ctl { min-width: 44px; min-height: 44px; border-radius: 999px; padding: 0; }
.ctl-label { font-size: 0.8em; opacity: 0.75; }

/* ---- Dark galaxy theme (shared look with Super App): near-black, thin neon #22d3ee accents, no purple ---- */
:root { --accent-bg-color: #22d3ee; --accent-fg-color: #05060a; --accent-color: #22d3ee;
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
.tile:hover { background-color: #11151e; box-shadow: inset 0 0 0 1px alpha(#22d3ee, 0.40), 0 0 16px alpha(#22d3ee, 0.08); }
.hero, .glow { border-radius: 22px; background-color: alpha(#0d1017, 0.95);
               background-image: radial-gradient(circle at 95% 0%, alpha(#22d3ee, 0.10), transparent 55%);
               box-shadow: inset 0 0 0 1px alpha(#22d3ee, 0.22); }
button.pill { border-radius: 999px; padding: 8px 18px; font-weight: bold; }
button.suggested-action { background: alpha(#22d3ee, 0.12); color: #22d3ee;
                          box-shadow: inset 0 0 0 1px alpha(#22d3ee, 0.55); }
button.suggested-action:hover { background: alpha(#22d3ee, 0.20); box-shadow: inset 0 0 0 1px #22d3ee, 0 0 14px alpha(#22d3ee, 0.25); }
button.suggested-action:disabled { background: alpha(white, 0.04); color: alpha(white, 0.35); box-shadow: inset 0 0 0 1px alpha(white, 0.08); }
progressbar progress { background: linear-gradient(90deg, #22d3ee, #3b82f6); border-radius: 999px; }
progressbar trough { background: alpha(white, 0.06); border-radius: 999px; }
.badge { background: alpha(#22d3ee, 0.10); color: #22d3ee; }
.tile-sub { color: #22d3ee; }
.boxed-list, list.boxed-list { background-color: alpha(#0d1017, 0.94); border-radius: 18px;
                               box-shadow: inset 0 0 0 1px alpha(white, 0.06); }
.find-title { color: white; }
.phone-mock { background: linear-gradient(160deg, #0f172a, #05060a); border-color: alpha(#22d3ee, 0.6); }
"""


class Tile(Gtk.Button):
    """A feature card: coloured icon, title, caption and an optional status badge."""

    def __init__(self, icon, color, title, caption, cb):
        super().__init__(css_classes=["card", "tile"], hexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        top = Gtk.Box(spacing=6)
        ic = Gtk.Image(icon_name=icon, pixel_size=22, css_classes=["tile-icon", color])
        top.append(ic)
        top.append(Gtk.Box(hexpand=True))
        self.badge = Gtk.Label(css_classes=["badge"], valign=Gtk.Align.START, visible=False)
        top.append(self.badge)
        box.append(top)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"], wrap=True))
        self.caption = Gtk.Label(label=caption, xalign=0, wrap=True, lines=2,
                                 css_classes=["caption", "dim-label"])
        box.append(self.caption)
        self.set_child(box)
        self.connect("clicked", lambda *_: cb())

    def set_status(self, badge=None, active=False, live=False, caption=None):
        self.badge.set_visible(bool(badge))
        self.badge.set_label(badge or "")
        (self.badge.add_css_class if live else self.badge.remove_css_class)("live")
        (self.add_css_class if active else self.remove_css_class)("active")
        if caption:
            self.caption.set_label(caption)


# ---------------------------------------------------------------- app picker
class AppPicker(Adw.Dialog):
    def __init__(self, win, apps):
        super().__init__(title="Open a phone app", content_width=420, content_height=560)
        self.win = win
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.search = Gtk.SearchEntry(placeholder_text="Search apps", margin_start=12, margin_end=12,
                                      margin_bottom=6)
        tv.add_top_bar(self.search)
        self.listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                   margin_start=12, margin_end=12, margin_top=6, margin_bottom=12,
                                   valign=Gtk.Align.START)
        for name, pkg in apps:
            row = Adw.ActionRow(title=name, subtitle=pkg, activatable=True, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="application-x-executable-symbolic", pixel_size=20))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, n=name, p=pkg: self.pick(n, p))
            row.key = (name + " " + pkg).lower()
            self.listbox.append(row)
        self.listbox.set_filter_func(lambda row: self.search.get_text().lower() in row.key)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.listbox)
        tv.set_content(scroll)
        self.set_child(tv)

    def pick(self, name, pkg):
        self.close()
        self.win.open_app(name, pkg)


# ---------------------------------------------------------------- phone file browser
PLACES = [("Camera", "camera-photo-symbolic", "/sdcard/DCIM/Camera"),
          ("Screenshots", "applets-screenshooter-symbolic", "/sdcard/Pictures/Screenshots"),
          ("Downloads", "folder-download-symbolic", "/sdcard/Download"),
          ("Documents", "folder-documents-symbolic", "/sdcard/Documents"),
          ("All files", "drive-harddisk-symbolic", "/sdcard")]
EXT_ICONS = [((".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"), "image-x-generic-symbolic"),
             ((".mp4", ".mkv", ".mov", ".3gp", ".webm"), "video-x-generic-symbolic"),
             ((".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".amr"), "audio-x-generic-symbolic"),
             ((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"), "x-office-document-symbolic"),
             ((".zip", ".rar", ".7z", ".apk"), "package-x-generic-symbolic")]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def list_phone_dir(serial, path):
    """Return [(name, is_dir, size, mtime)] newest first, folders first."""
    q = shlex.quote(path)
    out = adb("-s", serial, "shell", f"cd {q} 2>/dev/null && stat -c '%F|%s|%Y|%n' * 2>/dev/null", timeout=30)
    items = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[2].isdigit():
            kind, size, mtime, name = parts
            items.append((name, "directory" in kind, int(size) if size.isdigit() else 0, int(mtime)))
    items.sort(key=lambda i: (not i[1], -i[3]))
    return items


THUMB_DIR = os.path.expanduser("~/.cache/phone-connect/thumbs")
PREVIEW_DIR = os.path.expanduser("~/.cache/phone-connect/preview")
IMAGE_EXTS = EXT_ICONS[0][0]
THUMB_PX = 96


def _exif_orientation(tiff):
    """Read the EXIF orientation tag (0x0112) from a TIFF header block."""
    try:
        bo = "little" if tiff[:2] == b"II" else "big"
        ifd = int.from_bytes(tiff[4:8], bo)
        for i in range(int.from_bytes(tiff[ifd:ifd + 2], bo)):
            e = ifd + 2 + i * 12
            if int.from_bytes(tiff[e:e + 2], bo) == 0x0112:
                return int.from_bytes(tiff[e + 8:e + 10], bo)
    except Exception:
        pass
    return 1


def exif_thumbnail(head):
    """From the first bytes of a JPEG, return (embedded thumbnail JPEG bytes or None, orientation)."""
    if head[:2] != b"\xff\xd8":
        return None, 1
    i = 2
    while i + 4 <= len(head) and head[i] == 0xFF:
        marker, seglen = head[i + 1], int.from_bytes(head[i + 2:i + 4], "big")
        seg = head[i + 4:i + 2 + seglen]
        if marker == 0xE1 and seg[:6] == b"Exif\0\0":
            tiff = seg[6:]
            s, e = tiff.find(b"\xff\xd8", 8), tiff.rfind(b"\xff\xd9")
            return (tiff[s:e + 2] if s != -1 and e > s else None), _exif_orientation(tiff)
        if marker == 0xDA:
            break
        i += 2 + seglen
    return None, 1


def _oriented(pb, orientation):
    rot = {3: GdkPixbuf.PixbufRotation.UPSIDEDOWN, 6: GdkPixbuf.PixbufRotation.CLOCKWISE,
           8: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(orientation)
    return pb.rotate_simple(rot) if rot else pb


def phone_thumbnail(serial, full, size, mtime):
    """Small cached thumbnail (path to PNG) for a phone image, or None. Runs in a worker thread."""
    import hashlib
    os.makedirs(THUMB_DIR, exist_ok=True)
    out = os.path.join(THUMB_DIR, hashlib.sha1(f"{full}|{size}|{mtime}".encode()).hexdigest() + ".png")
    if os.path.exists(out):
        return out
    cat = lambda cmd: subprocess.run([ADB, "-s", serial, "exec-out", cmd], capture_output=True,
                                     timeout=60).stdout
    try:
        pb = None
        if full.lower().endswith((".jpg", ".jpeg")):
            thumb, orient = exif_thumbnail(cat(f"head -c 131072 {shlex.quote(full)}"))
            if thumb:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(thumb)
                loader.close()
                pb = _oriented(loader.get_pixbuf(), orient)
        if pb is None and size <= 8 * 1024 * 1024:      # no embedded preview: fetch the (small) file
            fd, tmp = tempfile.mkstemp()
            with os.fdopen(fd, "wb") as f:
                f.write(cat(f"cat {shlex.quote(full)}"))
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(tmp, THUMB_PX * 2, THUMB_PX * 2, True)
                pb = pb.apply_embedded_orientation() or pb
            finally:
                os.unlink(tmp)
        if pb is None:
            return None
        scale = THUMB_PX / min(pb.get_width(), pb.get_height())
        if scale < 1:
            pb = pb.scale_simple(max(1, int(pb.get_width() * scale)), max(1, int(pb.get_height() * scale)),
                                 GdkPixbuf.InterpType.BILINEAR)
        pb.savev(out, "png", [], [])
        return out
    except Exception:
        return None


class ImagePreview(Adw.Dialog):
    """Full-size look at one phone photo, with a Select toggle."""

    def __init__(self, browser, row, full):
        super().__init__(title=os.path.basename(full), content_width=720, content_height=760)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack()
        sp = Adw.StatusPage(title="Loading photo…")
        sp.set_child(Adw.Spinner(width_request=32, height_request=32))
        self.stack.add_named(sp, "loading")
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, vexpand=True, hexpand=True,
                               margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.stack.add_named(self.pic, "pic")
        tv.set_content(self.stack)
        bar = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=12)
        sel = Gtk.ToggleButton(active=row.check.get_active(), css_classes=["pill"])
        sel.set_child(Adw.ButtonContent(icon_name="object-select-symbolic", label="Select"))
        sel.connect("toggled", lambda b: row.check.set_active(b.get_active()))
        bar.append(sel)
        tv.add_bottom_bar(bar)
        self.set_child(tv)
        serial = browser.serial
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        dest = os.path.join(PREVIEW_DIR, os.path.basename(full))

        def work():
            if not os.path.exists(dest):
                adb("-s", serial, "pull", full, dest, timeout=300)
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(dest, 1600, 1600, True)
                return pb.apply_embedded_orientation() or pb
            except Exception:
                return None

        def done(pb):
            if pb:
                self.pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                self.stack.set_visible_child_name("pic")
            else:
                sp.set_title("Could not open this photo")
        bg(work, done)


class FileBrowser(Adw.Dialog):
    def __init__(self, win, start="/sdcard/DCIM/Camera"):
        super().__init__(title="Phone files", content_width=520, content_height=680)
        self.win = win
        self.serial = win.serial()
        self.path = start
        self.items = []
        self.selected = {}      # full path -> (size, is_dir)
        self.copying = False

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.up_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Up one folder")
        self.up_btn.connect("clicked", lambda *_: self.go(os.path.dirname(self.path)))
        hb.pack_start(self.up_btn)
        self.title = Adw.WindowTitle(title="Phone files")
        hb.set_title_widget(self.title)
        tv.add_top_bar(hb)

        # places
        places = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_bottom=6)
        self.place_btns = []
        for label, icon, path in PLACES:
            b = Gtk.ToggleButton(css_classes=["pill", "flat"])
            b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
            b.path = path
            b.connect("clicked", lambda btn: self.go(btn.path))
            places.append(b)
            self.place_btns.append(b)
        pscroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        pscroll.set_child(places)
        tv.add_top_bar(pscroll)

        tools = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_bottom=6)
        self.search = Gtk.SearchEntry(placeholder_text="Filter this folder", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        self.kind = Gtk.DropDown.new_from_strings(["All types", "Photos", "Videos", "Documents", "Audio"])
        self.kind.connect("notify::selected", lambda *_: self.listbox.invalidate_filter())
        tools.append(self.search)
        tools.append(self.kind)
        tv.add_top_bar(tools)

        self.listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                   margin_start=12, margin_end=12, margin_top=6, margin_bottom=12,
                                   valign=Gtk.Align.START)
        self.listbox.set_filter_func(self.filter_row)
        self.content = Gtk.Stack()
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.listbox)
        self.content.add_named(scroll, "list")
        loading = Adw.StatusPage(title="Loading…")
        loading.set_child(Adw.Spinner(width_request=32, height_request=32))
        self.content.add_named(loading, "loading")
        self.empty = Adw.StatusPage(icon_name="folder-symbolic", title="Empty folder")
        self.content.add_named(self.empty, "empty")
        tv.set_content(self.content)

        # bottom bar: selection summary + actions
        bar = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.sel_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        self.all_btn = Gtk.Button(label="Select all", css_classes=["flat"])
        self.all_btn.connect("clicked", lambda *_: self.select_all())
        self.copy_btn = Gtk.Button(css_classes=["suggested-action", "pill"])
        self.copy_btn.set_child(Adw.ButtonContent(icon_name="document-save-symbolic", label="Copy to laptop"))
        self.copy_btn.connect("clicked", lambda *_: self.choose_dest())
        bar.append(self.sel_label)
        bar.append(self.all_btn)
        bar.append(self.copy_btn)
        tv.add_bottom_bar(bar)
        self.set_child(tv)
        self.update_bar()
        self.connect("closed", lambda *_: setattr(self, "thumb_gen", -1))
        self.go(start)

    # ---- listing
    def go(self, path):
        if not path.startswith("/sdcard"):
            path = "/sdcard"
        self.path = path
        for b in self.place_btns:
            b.set_active(b.path == path)
        self.up_btn.set_sensitive(path != "/sdcard")
        name = next((p[0] for p in PLACES if p[2] == path), os.path.basename(path) or "Phone")
        self.title.set_title(name)
        self.title.set_subtitle(path.replace("/sdcard", "Phone", 1))
        self.search.set_text("")
        self.content.set_visible_child_name("loading")
        bg(lambda: list_phone_dir(self.serial, path), lambda items: self.show(path, items))

    def show(self, path, items):
        if path != self.path:
            return
        self.items = items
        while (r := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(r)
        if not items:
            self.content.set_visible_child_name("empty")
            return
        thumbs = []
        for name, is_dir, size, mtime in items:
            full = f"{path.rstrip('/')}/{name}"
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(mtime))
            row = Adw.ActionRow(title=name, use_markup=False,
                                subtitle=f"Folder · {when}" if is_dir else f"{human_size(size)} · {when}")
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=full in self.selected)
            check.connect("toggled", lambda c, f=full, s=size, d=is_dir: self.toggle(f, s, d, c.get_active()))
            frame = Gtk.Box(css_classes=["thumb"], width_request=48, height_request=48,
                            valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
            frame.append(Gtk.Image(icon_name=self.icon_for(name, is_dir), pixel_size=24, hexpand=True))
            row.add_prefix(frame)       # later prefixes sit further left: checkbox, then thumbnail
            row.add_prefix(check)
            row.frame = frame
            if not is_dir and name.lower().endswith(IMAGE_EXTS):
                thumbs.append((row, full, size, mtime))
                eye = Gtk.Button(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER,
                                 tooltip_text="Preview", css_classes=["flat"])
                eye.connect("clicked", lambda _b, r=row, f=full: ImagePreview(self, r, f).present(self))
                row.add_suffix(eye)
                click = Gtk.GestureClick()
                click.connect("released", lambda *_a, r=row, f=full: ImagePreview(self, r, f).present(self))
                frame.add_controller(click)
            if is_dir:
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.set_activatable(True)
                row.connect("activated", lambda _r, f=full: self.go(f))
            else:
                row.set_activatable_widget(check)
            row.meta = (name.lower(), is_dir)
            row.check = check
            self.listbox.append(row)
        self.content.set_visible_child_name("list")
        self.load_thumbs(thumbs)

    def load_thumbs(self, todo):
        """Fetch thumbnails one by one in the background; stop if the folder changes."""
        self.thumb_gen = gen = getattr(self, "thumb_gen", 0) + 1

        def put(row, png):
            if gen != self.thumb_gen or not png:
                return False
            try:
                tex = Gdk.Texture.new_from_filename(png)
            except GLib.Error:
                return False
            pic = Gtk.Picture(paintable=tex, content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                              width_request=48, height_request=48, hexpand=True)
            child = row.frame.get_first_child()
            if child:
                row.frame.remove(child)
            row.frame.append(pic)
            return False

        def work():
            for row, full, size, mtime in todo:
                if gen != self.thumb_gen:
                    return
                GLib.idle_add(put, row, phone_thumbnail(self.serial, full, size, mtime))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def icon_for(name, is_dir):
        if is_dir:
            return "folder-symbolic"
        low = name.lower()
        return next((icon for exts, icon in EXT_ICONS if low.endswith(exts)), "text-x-generic-symbolic")

    def filter_row(self, row):
        name, is_dir = row.meta
        if self.search.get_text().lower() not in name:
            return False
        k = self.kind.get_selected()
        if k == 0 or is_dir:            # folders stay visible so you can open them
            return True
        exts = {1: EXT_ICONS[0][0], 2: EXT_ICONS[1][0], 3: EXT_ICONS[3][0], 4: EXT_ICONS[2][0]}[k]
        return name.endswith(exts)

    # ---- selection
    def toggle(self, full, size, is_dir, on):
        if on:
            self.selected[full] = (size, is_dir)
        else:
            self.selected.pop(full, None)
        self.update_bar()

    def select_all(self):
        rows = []
        i = 0
        while (r := self.listbox.get_row_at_index(i)) is not None:
            if r.get_child_visible() and not r.meta[1]:
                rows.append(r)
            i += 1
        on = not all(r.check.get_active() for r in rows)
        for r in rows:
            r.check.set_active(on)

    def update_bar(self):
        n = len(self.selected)
        size = sum(s for s, d in self.selected.values() if not d)
        folders = sum(1 for _s, d in self.selected.values() if d)
        if n:
            txt = f"{n} selected · {human_size(size)}" + (f" + {folders} folder(s)" if folders else "")
        else:
            txt = "Tick the files you want"
        self.sel_label.set_label(txt)
        self.copy_btn.set_sensitive(n > 0 and not self.copying)

    # ---- copy
    def choose_dest(self):
        dlg = Gtk.FileDialog(title="Copy to which folder?", accept_label="Copy here")
        last = self.win.cfg.get("files_dest", os.path.expanduser("~/Downloads"))
        if os.path.isdir(last):
            dlg.set_initial_folder(Gio.File.new_for_path(last))

        def chosen(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            dest = folder.get_path()
            self.win.cfg["files_dest"] = dest
            save_cfg(self.win.cfg)
            self.copy(dest)
        dlg.select_folder(self.win, None, chosen)

    def copy(self, dest):
        paths = list(self.selected)
        self.copying = True
        self.update_bar()

        def progress(i):
            self.sel_label.set_label(f"Copying {i + 1} of {len(paths)}…")
            return False

        def work():
            ok = 0
            for i, p in enumerate(paths):
                GLib.idle_add(progress, i)
                if "pulled" in adb("-s", self.serial, "pull", "-a", p, dest + "/", timeout=3600):
                    ok += 1
            return ok

        def done(ok):
            self.copying = False
            self.selected.clear()
            i = 0
            while (r := self.listbox.get_row_at_index(i)) is not None:
                r.check.set_active(False)
                i += 1
            self.update_bar()
            self.win.toast(f"Copied {ok} of {len(paths)} to {dest.replace(os.path.expanduser('~'), '~')}", dest)
        bg(work, done)


# ---------------------------------------------------------------- main window
GSCONNECT = "gsconnect@andyholmes.github.io"
# Recommended settings (Settings › Reset restores these)
DEFAULTS = {
    "phone_mode": 0,         # while mirroring: 0 phone screen dark, 1 stays on, 2 phone’s own timeout
    "wake_on_start": True,
    "lock_on_close": False,
    "on_lock": 0,            # phone locked while mirroring: 0 close the phone window, 1 keep it open
    "view_only": False,
    "clipboard": True,
    "uhid_kb": False,
    "on_top": False,
    "borderless": False,
    "fullscreen": False,
    "audio_out": 0,          # 0 laptop, 1 laptop + phone, 2 phone only
    "size": 1,               # 1024 / 1600 / full
    "fps": 1,                # 30 / 60 / 120
    "auto_connect": True,
    "rec_audio": True,
    "cam_res": 1,            # 720p / 1080p
}
PICS = os.path.expanduser("~/Pictures/Phone")
VIDS = os.path.expanduser("~/Videos/Phone")
WEBCAM_SETUP = (
    "set -e; "
    "echo 'options v4l2loopback devices=1 video_nr=42 card_label=\"Phone Camera\" exclusive_caps=1' "
    "> /etc/modprobe.d/phone-connect-webcam.conf; "
    "echo v4l2loopback > /etc/modules-load.d/phone-connect-webcam.conf; "
    "modinfo v4l2loopback >/dev/null 2>&1 || apt-get install -y v4l2loopback-dkms; "
    "modprobe -r v4l2loopback 2>/dev/null || true; modprobe v4l2loopback")


def webcam_device():
    base = "/sys/class/video4linux"
    try:
        for d in sorted(os.listdir(base)):
            with open(f"{base}/{d}/name") as f:
                if "Phone Camera" in f.read():
                    return f"/dev/{d}"
    except OSError:
        pass
    return None


def gsconnect_enabled():
    return GSCONNECT in Gio.Settings.new("org.gnome.shell").get_strv("enabled-extensions")


def gsconnect_state():
    """missing | off (installed, turned off) | needs-login (on, loads after re-login) | ready (running)."""
    installed = any(os.path.exists(os.path.join(b, GSCONNECT, "metadata.json")) for b in
                    ("/usr/share/gnome-shell/extensions", os.path.expanduser("~/.local/share/gnome-shell/extensions")))
    if not installed:
        return "missing"
    if not gsconnect_enabled():
        return "off"
    info = subprocess.run(["gnome-extensions", "info", GSCONNECT], capture_output=True, text=True)
    return "ready" if info.returncode == 0 and "INACTIVE" not in info.stdout.upper() else "needs-login"


def set_gsconnect(on):
    """Turn GSConnect on/off. Off takes effect now; on takes effect now if GNOME already loaded it,
    otherwise after the next login."""
    s = Gio.Settings.new("org.gnome.shell")
    enabled = [e for e in s.get_strv("enabled-extensions") if e != GSCONNECT]
    disabled = [e for e in s.get_strv("disabled-extensions") if e != GSCONNECT]
    s.set_strv("enabled-extensions", enabled + ([GSCONNECT] if on else []))
    s.set_strv("disabled-extensions", disabled + ([] if on else [GSCONNECT]))
    Gio.Settings.sync()
    subprocess.run(["gnome-extensions", "enable" if on else "disable", GSCONNECT], capture_output=True)


def open_path(path):
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


def svg_picture(name, w, h):
    """Crisp (2x) picture of an SVG from the assets folder; empty box if missing."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(os.path.join(ASSETS, name), w * 2, h * 2, True)
        pic = Gtk.Picture(paintable=Gdk.Texture.new_for_pixbuf(pb), can_shrink=True,
                          halign=Gtk.Align.CENTER)
        pic.set_size_request(w, h)
        return pic
    except Exception:
        return Gtk.Box()


class FindHeader(Gtk.Box):
    """Illustration + title + description + action buttons (StatusPage-like API)."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=28, margin_bottom=12)
        self.append(svg_picture("connect-illustration.svg", 260, 150))
        self.t = Gtk.Label(css_classes=["find-title"], wrap=True, justify=Gtk.Justification.CENTER,
                           margin_top=12)
        self.d = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER,
                           use_markup=True)
        self.append(self.t)
        self.append(self.d)
        self.slot = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=14)
        self.append(self.slot)

    def set_title(self, text):
        self.t.set_label(text)

    def set_description(self, markup):
        self.d.set_markup(markup)

    def set_child(self, w):
        self.slot.append(w)


class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Phone Connect", default_width=480, default_height=820)
        self.cfg = load_cfg()
        self.cfg.pop("manual_off", None)
        if "audio" in self.cfg:              # migrate the old on/off sound setting
            self.cfg.setdefault("audio_out", 0 if self.cfg.pop("audio") else 2)
        if self.cfg.pop("screen_off_v1", None) is None and "screen_off" in self.cfg:
            self.cfg.pop("screen_off")       # old default turned the phone off; use the new default
        self.cfg["screen_off_v1"] = True
        save_cfg(self.cfg)
        if "phone_mode" not in self.cfg and ("screen_off" in self.cfg or "keep_on" in self.cfg):
            self.cfg["phone_mode"] = 0 if self.cfg.get("screen_off") else (1 if self.cfg.get("keep_on", True) else 2)
        for k in ("screen_off", "keep_on", "awake"):
            self.cfg.pop(k, None)
        save_cfg(self.cfg)
        if not self.opt("auto_connect"):
            self.cfg["manual_off"] = True    # wait for "Search again"
        self.avahi = Avahi()
        self.services = {}      # mDNS name -> "ip:port"
        self.errors = {}          # phone key -> 'refused' / 'unpaired'
        self.connecting = None
        self.connected_key = None
        if self.cfg.get("lan_mode"):        # Wi-Fi link: be reachable as soon as the app runs
            GLib.timeout_add(800, lambda: (self.ensure_server() and bg(self.comp_srv.start_lan,
                                                                      lambda _r: self.refresh()), False)[1])
        self.extend_on = False    # tablet used as an extra monitor (GNOME Remote Desktop, extend mode)
        self.extend_serial = None
        self.chosen = None        # device picked this session (asked on every start when 2+ are paired)
        self.phone_groups = []
        self.phones_sig = None
        self.devs = []
        self.tasks = {}         # name -> Popen (scrcpy sessions)
        self.last_try = 0
        self.battery = None
        self.charging = False
        self.android = None
        self.apps = None
        self.gsc = gsconnect_state()

        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)   # above ~/.config/gtk-4.0/gtk.css

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.wtitle = Adw.WindowTitle(title="Phone Connect")
        header.set_title_widget(self.wtitle)
        menu = Gio.Menu()
        menu.append("Your phones", "win.phones")
        menu.append("Pair a new phone", "win.pair")
        menu.append("Settings", "win.settings")
        menu.append("Disconnect phone", "win.disconnect")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))
        for name, fn in (("phones", self.open_phones), ("pair", self.open_pair), ("settings", self.open_settings),
                         ("disconnect", self.disconnect)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=fn: f())
            self.add_action(act)
        tv.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tv.set_content(self.stack)
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        self.build_setup_page()
        self.build_find_page()
        self.build_home_page()

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        self.connect_browser = self.avahi.browse(CONNECT_T, self.on_service, self.on_service_gone)
        self.connect("close-request", self.on_close)
        self.refresh()
        self.poll()
        GLib.timeout_add_seconds(3, self.poll)
        GLib.timeout_add_seconds(30, self.poll_battery)
        GLib.timeout_add_seconds(3, self.check_wake)

    # ================================================================ pages
    def build_setup_page(self):
        p = Adw.StatusPage(icon_name="system-software-install-symbolic", title="One-time setup",
                           description="Phone Connect needs the free “scrcpy” tool to show your phone.")
        b = Gtk.Button(label="Install", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        b.connect("clicked", self.install_scrcpy)
        self.install_btn = b
        p.set_child(b)
        self.stack.add_named(p, "setup")

    def build_find_page(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=420, margin_start=16, margin_end=16, margin_bottom=24)
        scroll.set_child(clamp)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(col)
        self.find_page = FindHeader()
        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        b1 = Gtk.Button(css_classes=["pill", "suggested-action"])
        b1.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Pair a phone"))
        b1.connect("clicked", lambda *_: self.open_pair())
        b2 = Gtk.Button(css_classes=["pill"])
        b2.set_child(Adw.ButtonContent(icon_name="view-refresh-symbolic", label="Search again"))
        b2.connect("clicked", lambda *_: self.search_again())
        b3 = Gtk.Button(css_classes=["pill"], visible=False)
        b3.set_child(Adw.ButtonContent(icon_name="network-wireless-symbolic", label="Use the Wi-Fi link (no cable)"))
        b3.connect("clicked", lambda *_: self.lan_setup())
        self.lan_btn = b3
        btns.append(b1)
        btns.append(b2)
        btns.append(b3)
        self.find_page.set_child(btns)
        col.append(self.find_page)
        self.phones_group = Adw.PreferencesGroup(title="Your phones", visible=False)
        self.phone_groups.append(self.phones_group)
        col.append(self.phones_group)
        grp = Adw.PreferencesGroup()
        exp = Adw.ExpanderRow(title="How to turn on Wireless debugging", icon_name="help-about-symbolic")
        exp.add_row(Gtk.Label(label=HOWTO, use_markup=True, wrap=True, xalign=0, margin_top=12,
                              margin_bottom=12, margin_start=12, margin_end=12))
        grp.add(exp)
        col.append(grp)
        self.stack.add_named(scroll, "find")

    @staticmethod
    def chip(parent, icon):
        box = Gtk.Box(spacing=5, css_classes=["chip"])
        img = Gtk.Image(icon_name=icon, pixel_size=14)
        lbl = Gtk.Label()
        box.append(img)
        box.append(lbl)
        parent.append(box)
        return img, lbl

    def section(self, col, title, tiles, icon=None, extra=None):
        head = Gtk.Box(spacing=8, margin_top=10)
        if icon:
            head.append(Gtk.Image(icon_name=icon, pixel_size=16, css_classes=["section-icon"]))
        head.append(Gtk.Label(label=title, xalign=0, css_classes=["section"], hexpand=True))
        if extra:
            head.append(extra)
        col.append(head)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        for i, t in enumerate(tiles):
            grid.attach(t, i % 2, i // 2, 1, 1)
        col.append(grid)
        return [head, grid]

    def build_home_page(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=460, margin_start=16, margin_end=16, margin_bottom=24)
        scroll.set_child(clamp)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12)
        clamp.set_child(col)
        self.sleep_banner = Adw.Banner(title="Your phone screen is off, so the phone window is dark",
                                       button_label="Wake phone")
        self.sleep_banner.connect("button-clicked", lambda *_: self.wake_unlock())
        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page_box.append(self.sleep_banner)

        # --- hero: phone illustration + main action
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                       margin_bottom=8)
        mock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["phone-mock"],
                       halign=Gtk.Align.CENTER, margin_bottom=8)
        mock.append(Gtk.Box(css_classes=["notch"], halign=Gtk.Align.CENTER, margin_top=10))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True,
                        valign=Gtk.Align.START, margin_top=26)
        self.mock_time = Gtk.Label(css_classes=["mock-time"])
        self.mock_date = Gtk.Label(css_classes=["mock-date"])
        inner.append(self.mock_time)
        inner.append(self.mock_date)
        mock.append(inner)
        status = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_bottom=14)
        self.mock_icon = Gtk.Image(icon_name="network-wireless-symbolic", pixel_size=12)
        self.mock_batt_icon = Gtk.Image(icon_name="battery-level-50-symbolic", pixel_size=12)
        self.mock_batt = Gtk.Label(css_classes=["mock-date"])
        for w in (self.mock_icon, self.mock_batt_icon, self.mock_batt):
            status.append(w)
        mock.append(status)
        hero.append(mock)
        self.h_title = Gtk.Label(css_classes=["title-1"])
        self.h_sub = Gtk.Label(css_classes=["dim-label"], visible=False)
        hero.append(self.h_title)
        chips = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_top=4)
        self.chip_conn, self.chip_conn_l = self.chip(chips, "network-wireless-symbolic")
        self.chip_batt, self.chip_batt_l = self.chip(chips, "battery-level-50-symbolic")
        self.chip_os, self.chip_os_l = self.chip(chips, "phone-symbolic")
        hero.append(chips)
        self.switch_btn = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["flat", "pill"], visible=False)
        self.switch_btn.set_child(Adw.ButtonContent(icon_name="phone-symbolic", label="Switch phone"))
        self.switch_btn.connect("clicked", lambda *_: self.open_phones())
        hero.append(self.switch_btn)
        self.mirror_btn = Gtk.Button(margin_top=12, css_classes=["pill", "suggested-action"],
                                     halign=Gtk.Align.CENTER)
        self.mirror_content = Adw.ButtonContent(icon_name="video-display-symbolic", label="Show phone screen")
        self.mirror_btn.set_child(self.mirror_content)
        self.mirror_btn.connect("clicked", lambda *_: self.toggle("mirror", self.mirror_cmd))
        hero.append(self.mirror_btn)
        self.mirror_hint = Gtk.Label(label="Use it with your mouse and keyboard.\n"
                                           "Phone window dark? Press Wake & unlock, then type your PIN.",
                                     css_classes=["caption", "dim-label"], wrap=True,
                                     justify=Gtk.Justification.CENTER)
        hero.append(self.mirror_hint)
        self.wifi_hint = Gtk.Label(label="Connected over the Wi-Fi link (no cable).\n"
                                         "Camera, audio and second-screen features work; mirroring needs debugging.",
                                   css_classes=["caption", "dim-label"], wrap=True, visible=False,
                                   justify=Gtk.Justification.CENTER, margin_top=8)
        hero.append(self.wifi_hint)
        ctl = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=10)
        self.ctl_row = ctl
        for icon, label, tip, cb in (
                ("changes-allow-symbolic", "Unlock", "Wake the phone and open the PIN screen", self.wake_unlock),
                ("system-lock-screen-symbolic", "Lock", "Turn the phone screen off and lock it",
                 lambda: self.keyevent("KEYCODE_SLEEP")),
                ("go-previous-symbolic", "Back", "Back", lambda: self.keyevent("KEYCODE_BACK")),
                ("go-home-symbolic", "Home", "Home screen", lambda: self.keyevent("KEYCODE_HOME")),
                ("view-paged-symbolic", "Recents", "Recent apps", lambda: self.keyevent("KEYCODE_APP_SWITCH")),
                ("audio-volume-low-symbolic", "Vol −", "Volume down", lambda: self.keyevent("KEYCODE_VOLUME_DOWN")),
                ("audio-volume-high-symbolic", "Vol +", "Volume up", lambda: self.keyevent("KEYCODE_VOLUME_UP"))):
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, halign=Gtk.Align.CENTER, css_classes=["ctl"])
            b.connect("clicked", lambda _b, f=cb: f())
            v.append(b)
            v.append(Gtk.Label(label=label, css_classes=["ctl-label"]))
            ctl.append(v)
        hero.append(ctl)
        col.append(hero)

        self.t_app = Tile("view-app-grid-symbolic", "c-blue", "Open an app",
                          "Run one phone app in its own window", self.pick_app)
        self.t_shot = Tile("camera-photo-symbolic", "c-teal", "Screenshot",
                           "Saved to Pictures › Phone", self.screenshot)
        self.t_rec = Tile("media-record-symbolic", "c-red", "Record screen",
                          "Saved to Videos › Phone", lambda: self.toggle("record", self.record_cmd))
        self.adb_only = self.section(col, "Screen", [self.t_app, self.t_shot, self.t_rec], "video-display-symbolic")

        self.t_cam = Tile("camera-web-symbolic", "c-green", "Phone as webcam",
                          "Use the phone camera in Meet, Zoom, OBS", self.webcam)
        self.t_flip = Tile("camera-switch-symbolic", "c-teal", "Switch camera", "", self.flip_camera)
        self.t_mic = Tile("audio-input-microphone-symbolic", "c-red", "Use as microphone",
                          "The device mic as a laptop input", self.toggle_mic)
        self.t_spk = Tile("audio-speakers-symbolic", "c-orange", "Use as speaker",
                          "Laptop sound plays on the device", self.toggle_speaker)
        self.section(col, "Camera & audio", [self.t_cam, self.t_flip, self.t_mic, self.t_spk], "camera-web-symbolic")

        self.t_send = Tile("document-send-symbolic", "c-orange", "Send files",
                           "Or drop files on this window", self.choose_files)
        self.t_photos = Tile("folder-pictures-symbolic", "c-yellow", "Browse phone files",
                             "Pick any photos, videos or files to copy", self.browse_files)
        self.adb_only += self.section(col, "Files", [self.t_send, self.t_photos], "folder-symbolic")

        self.t_notif = Tile("preferences-system-notifications-symbolic", "c-pink", "Notifications & SMS",
                            "Phone alerts on the laptop (GSConnect)", self.gsconnect)
        self.adb_only += self.section(col, "Notifications", [self.t_notif], "preferences-system-notifications-symbolic")

        self.t_panel = Tile("preferences-desktop-apps-symbolic", "c-blue", "Control panel",
                            "Volume, media, brightness and app buttons for the laptop",
                            lambda: self.companion_open("panel"))
        self.t_monitor = Tile("utilities-system-monitor-symbolic", "c-green", "Live monitor",
                              "Clock, CPU, RAM, Docker, battery — always on", lambda: self.companion_open("monitor"))
        self.t_pad = Tile("input-tablet-symbolic", "c-teal", "Drawing pad",
                          "Pen and touch draw on the laptop, with pressure", lambda: self.companion_open("draw"))
        # "Show on" picker: which device these pages open on
        self.screen_keys = []
        self.screen_model = Gtk.StringList()
        self.screen_dd = Gtk.DropDown(model=self.screen_model, valign=Gtk.Align.CENTER,
                                      tooltip_text="Which device these open on", css_classes=["flat"])
        self.screen_dd_h = self.screen_dd.connect("notify::selected", self.on_screen_pick)
        show_on = Gtk.Box(spacing=4)
        show_on.append(Gtk.Label(label="Show on", css_classes=["dim-label"]))
        show_on.append(self.screen_dd)
        self.t_disp = Tile("video-display-symbolic", "c-purple", "Extra display",
                           "Use it as another monitor for the laptop", self.extra_display)
        self.t_lan = Tile("network-wireless-symbolic", "c-blue", "Wi-Fi link",
                          "Use these on a tablet without a cable", self.lan_setup)
        self.section(col, "Second screen", [self.t_disp, self.t_panel, self.t_monitor, self.t_pad, self.t_lan],
                     "tablet-symbolic", show_on)
        self.screen_hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"], visible=False)
        col.append(self.screen_hint)
        scroll.set_vexpand(True)
        page_box.append(scroll)
        self.stack.add_named(page_box, "home")

    def open_settings(self):
        dlg = Adw.PreferencesDialog(title="Settings", search_enabled=True)

        def page(title, icon):
            p = Adw.PreferencesPage(title=title, icon_name=icon)
            dlg.add(p)
            return p

        def group(p, title, desc=None):
            g = Adw.PreferencesGroup(title=GLib.markup_escape_text(title),
                                     description=GLib.markup_escape_text(desc) if desc else None)
            p.add(g)
            return g

        def switch(g, key, title, subtitle, icon):
            r = Adw.SwitchRow(title=title, subtitle=subtitle, active=self.opt(key), use_markup=False)
            r.add_prefix(Gtk.Image(icon_name=icon))
            r.connect("notify::active", lambda row, _p: self.set_opt(key, row.get_active()))
            g.add(r)

        def combo(g, key, title, subtitle, options, icon):
            r = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(options),
                             selected=self.opt(key), use_markup=False)
            r.add_prefix(Gtk.Image(icon_name=icon))
            r.connect("notify::selected", lambda row, _p: self.set_opt(key, row.get_selected()))
            g.add(r)

        # ---- Mirroring
        p = page("Mirroring", "video-display-symbolic")
        g = group(p, "Your phone while mirroring", "Changes apply right away, also while the phone screen is shown.")
        combo(g, "phone_mode", "Phone screen while mirroring",
              "Dark: use the phone only from the laptop; it stays unlocked and never sleeps. "
              "On: stays lit and never sleeps. Phone timeout: sleeps like normal — the laptop window goes black too.",
              ["Dark (use from laptop)", "On", "Phone timeout"], "display-brightness-symbolic")
        switch(g, "wake_on_start", "Wake phone when mirroring starts",
               "Turns the phone screen on if it was asleep.", "weather-clear-symbolic")
        switch(g, "lock_on_close", "Lock phone when mirroring stops",
               "Turns the phone screen off when you close the phone window.", "system-lock-screen-symbolic")
        combo(g, "on_lock", "When you lock the phone",
              "What happens to the laptop window if you press the phone’s power button while mirroring.",
              ["Close the phone window", "Keep the window open"], "system-lock-screen-symbolic")
        g = group(p, "Control")
        switch(g, "view_only", "View only", "Show the screen without letting the laptop control it.",
               "view-reveal-symbolic")
        switch(g, "clipboard", "Shared clipboard",
               "Copy on one device, paste on the other.", "edit-paste-symbolic")
        switch(g, "uhid_kb", "Physical-keyboard mode",
               "Better typing in every language. The phone may ask you to pick a keyboard layout once.",
               "input-keyboard-symbolic")
        g = group(p, "Window")
        switch(g, "on_top", "Always on top", "Keep the phone window above other windows.",
               "view-pin-symbolic")
        switch(g, "borderless", "No window border", "Hide the title bar for a cleaner look.",
               "view-fullscreen-symbolic")
        switch(g, "fullscreen", "Start in full screen", "Press Alt+F to leave full screen.",
               "view-fullscreen-symbolic")

        # ---- Sound & quality
        p = page("Sound & Quality", "audio-speakers-symbolic")
        g = group(p, "Sound")
        combo(g, "audio_out", "Play phone sound on",
              "Where music, videos and calls are heard while mirroring.",
              ["Laptop", "Laptop and phone", "Phone only"], "audio-speakers-symbolic")
        g = group(p, "Picture", "Lower settings are smoother on slow Wi-Fi.")
        combo(g, "size", "Resolution", None, ["Smooth (1024 px)", "Balanced (1600 px)", "Sharpest (full)"],
              "video-display-symbolic")
        combo(g, "fps", "Frame rate", None, ["30 fps — saves battery", "60 fps — smooth", "120 fps — fastest"],
              "media-playlist-repeat-symbolic")

        # ---- General
        p = page("General", "emblem-system-symbolic")
        g = group(p, "Connection")
        switch(g, "auto_connect", "Connect automatically",
               "Connect to your phone as soon as the app opens and finds it on Wi-Fi.",
               "network-wireless-symbolic")
        g = group(p, "Phone notifications")
        state = gsconnect_state()
        if state == "missing":
            r = Adw.ActionRow(title="Notifications & SMS", subtitle="Not set up yet — use the tile on the main screen.",
                              use_markup=False)
            r.add_prefix(Gtk.Image(icon_name="preferences-system-notifications-symbolic"))
            g.add(r)
        else:
            r = Adw.SwitchRow(title="Notifications & SMS (GSConnect)", use_markup=False, active=state != "off",
                              subtitle="Show phone notifications and messages on the laptop. "
                                       "Turning on may need one log out and back in.")
            r.add_prefix(Gtk.Image(icon_name="preferences-system-notifications-symbolic"))
            r.connect("notify::active", lambda row, _p: self.set_notifications(row.get_active()))
            g.add(r)
        g = group(p, "Recording & webcam")
        switch(g, "rec_audio", "Record phone sound", "Include sound in screen recordings.",
               "media-record-symbolic")
        combo(g, "cam_res", "Webcam resolution", None, ["720p", "1080p"], "camera-web-symbolic")
        g = group(p, "Saved files")
        for title, path in (("Screenshots & photos", PICS), ("Screen recordings", VIDS)):
            r = Adw.ActionRow(title=title, subtitle=path.replace(os.path.expanduser("~"), "~"),
                              activatable=True, use_markup=False)
            r.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            r.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
            r.connect("activated", lambda _r, p=path: (os.makedirs(p, exist_ok=True), open_path(p)))
            g.add(r)
        g = group(p, "")
        reset = Adw.ButtonRow(title="Reset all settings to recommended", start_icon_name="edit-undo-symbolic")

        def do_reset(*_):
            for k in DEFAULTS:
                self.cfg.pop(k, None)
            save_cfg(self.cfg)
            dlg.close()
            self.toast("Settings reset")
            self.refresh()
        reset.connect("activated", do_reset)
        g.add(reset)
        dlg.present(self)

    def opt(self, key):
        return self.cfg.get(key, DEFAULTS[key])

    MIRROR_KEYS = {"wake_on_start", "lock_on_close", "view_only", "clipboard",
                   "uhid_kb", "on_top", "borderless", "fullscreen", "audio_out", "size", "fps", "phone_mode"}

    def set_opt(self, key, value):
        self.cfg[key] = value
        save_cfg(self.cfg)
        if key in self.MIRROR_KEYS and self.running("mirror"):
            self.restart_mirror()

    @staticmethod
    def _wait_quietly(p):
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()

    def restart_mirror(self):
        """Apply changed settings to the open phone window by reopening it (debounced)."""
        if getattr(self, "restart_pending", False):
            return
        self.restart_pending = True

        def later():
            p = self.tasks.get("mirror")
            if p and p.poll() is None:
                self.stop_task("mirror")
                bg(lambda: self._wait_quietly(p), lambda _r: GLib.timeout_add(700, start))
            else:
                self.restart_pending = False       # window closed meanwhile: don't reopen it
            return False

        def start():
            self.restart_pending = False
            if self.serial() and not self.running("mirror"):
                self.toggle("mirror", self.mirror_cmd)
                self.toast("Settings applied")
            return False
        GLib.timeout_add(400, later)     # wait for quick successive toggles

    # ================================================================ state
    def toast(self, msg, open_target=None):
        t = Adw.Toast(title=GLib.markup_escape_text(msg[:180]), timeout=5)
        if open_target:
            t.set_button_label("Open")
            t.connect("button-clicked", lambda *_: open_path(open_target))
        self.toasts.add_toast(t)

    def companion_addr(self):
        k = self.comp_key()
        return (self.online().get(k) or self.tcp_addr(k)) if k else None

    def needs_choice(self):
        """True until the user picks which device to use, when more than one is paired."""
        return self.chosen is None and len(self.paired_keys()) > 1

    def comp_key(self):
        k = self.cfg.get("companion")
        return None if k == self.chosen else k     # picked the tablet itself: it is the main device now

    def usb_serial_of(self, key):
        sn = key[4:] if key and key.startswith("adb-") else None
        return sn if sn and any(d["serial"] == sn and d["state"] == "device" for d in self.devs) else None

    def key_serials(self, k):
        """Every adb serial a paired device may show up as (Wi-Fi address or USB cable)."""
        if not k:
            return set()
        return {a for a in (self.online().get(k), self.tcp_addr(k), k[4:] if k.startswith("adb-") else None) if a}

    def companion_serials(self):
        return self.key_serials(self.comp_key())

    def main_serial(self):
        """The device used for mirroring etc.: the one picked this session, never the companion screen."""
        if self.needs_choice():
            return None
        comp = self.companion_serials()
        ready = [d["serial"] for d in self.devs if d["state"] == "device" and d["serial"] not in comp]
        if self.chosen:
            mine = self.key_serials(self.chosen)
            ready = [x for x in ready if x in mine]
        return next((x for x in ready if ":" in x), None) or next(iter(ready), None)

    def companion_serial(self):
        if self.needs_choice():
            return None
        comp = self.companion_serials()
        ready = [d["serial"] for d in self.devs if d["state"] == "device" and d["serial"] in comp]
        return next((x for x in ready if ":" in x), None) or next(iter(ready), None)

    def serial(self):
        if self.chosen:                  # stick to the device picked this session
            return self.main_serial()
        return self.main_serial() or self.companion_serial()

    def publish_status(self, state):
        if state != getattr(self, "last_status", None):
            self.last_status = state
            write_status(dict(state, time=time.time()))

    def running(self, name):
        p = self.tasks.get(name)
        return p is not None and p.poll() is None

    def refresh(self):
        if not (os.path.exists(ADB) and shutil.which("scrcpy")):
            self.stack.set_visible_child_name("setup")
            self.wtitle.set_subtitle("")
            return
        self.fill_phones()
        con = [d for d in self.devs if d["state"] == "device"]
        wifi_only = not (con and self.serial()) and self.lan_alive()
        for w in self.adb_only + [self.mirror_btn, self.mirror_hint, self.ctl_row]:
            w.set_visible(not wifi_only)
        self.wifi_hint.set_visible(wifi_only)
        if (con and self.serial()) or wifi_only:
            if wifi_only:
                d = {"model": self.comp_srv.lan_device or "Tablet", "wifi": True, "serial": None}
            else:
                d = next((x for x in con if x["serial"] == self.serial()), con[0])
            self.stack.set_visible_child_name("home")
            self.h_title.set_label(d["model"])
            how = "Wi-Fi" if d["wifi"] else "USB"
            conn_icon = "network-wireless-symbolic" if d["wifi"] else "drive-removable-media-symbolic"
            self.mock_icon.set_from_icon_name(conn_icon)
            self.mock_time.set_label(time.strftime("%H:%M"))
            self.mock_date.set_label(time.strftime("%a, %d %b"))
            b = None if wifi_only else self.battery
            if b is not None:
                lvl = min(100, round(b / 10) * 10)
                charging = self.charging
                icon = (f"battery-level-{lvl}-charged-symbolic" if charging and lvl == 100 else
                        f"battery-level-{lvl}-charging-symbolic" if charging else f"battery-level-{lvl}-symbolic")
                self.mock_batt_icon.set_from_icon_name(icon)
                self.chip_batt.set_from_icon_name(icon)
                self.mock_batt.set_label(f"{b}%")
                self.chip_batt_l.set_label(f"{b}%" + (" · Charging" if charging else ""))
            self.chip_batt.get_parent().set_visible(b is not None)
            self.mock_batt_icon.set_visible(b is not None)
            self.chip_conn.set_from_icon_name(conn_icon)
            self.chip_conn_l.set_label(how)
            self.chip_os_l.set_label(f"Android {self.android}" if self.android else "")
            self.chip_os.get_parent().set_visible(bool(self.android) and not wifi_only)
            self.wtitle.set_subtitle(f"{d['model']} · {how}")
            m = self.running("mirror")
            self.publish_status({"model": d["model"], "how": how, "battery": b, "charging": bool(self.charging),
                                 "mirror": m})
            self.mirror_content.set_label("Hide phone screen" if m else "Show phone screen")
            self.mirror_btn.remove_css_class("suggested-action" if m else "destructive-action")
            self.mirror_btn.add_css_class("destructive-action" if m else "suggested-action")
            self.t_rec.set_status("● REC" if self.running("record") else None,
                                  active=self.running("record"), live=True)
            cam_on = self.running("webcam")
            facing = ["Back", "Front"][self.cfg.get("facing", 0)]
            self.t_cam.set_status("On" if cam_on else (None if webcam_device() else "Set up"),
                                  active=cam_on, live=cam_on,
                                  caption=("Use the camera in Meet, Zoom, OBS"
                                           if self.has_camera() and not self.lan_alive()
                                           else "Through the device's browser — keep its page open"))
            self.t_flip.set_status(facing, caption=f"Using the {facing.lower()} camera")
            mic_on = self.running("mic")
            self.t_mic.set_status("On" if mic_on else None, active=mic_on, live=mic_on)
            spk_on = self.running("speaker")
            self.t_spk.set_status("On" if spk_on else None, active=spk_on, live=spk_on)
            self.t_notif.set_status({"ready": "On", "needs-login": "Log out once", "off": "Off",
                                     "missing": "Set up"}[self.gsc], active=self.gsc == "ready")
            self.fill_screen_picker()
            lan_on = bool(getattr(self, "comp_srv", None) and self.comp_srv.lan_server)
            self.t_lan.set_status(("Connected" if self.lan_alive() else "On") if lan_on else None,
                                  active=self.lan_alive(), live=self.lan_alive())
            self.t_disp.set_status("On" if self.extend_on else None, active=self.extend_on, live=self.extend_on)
            self.t_pad.set_status(None if companion.uinput_ready() else "Set up")
            apps_open = sum(1 for n in self.tasks if n.startswith("app:") and self.running(n))
            self.t_app.set_status(f"{apps_open} open" if apps_open else None, active=bool(apps_open))
            return
        self.wtitle.set_subtitle("Not connected")
        self.publish_status({"model": None})
        self.stack.set_visible_child_name("find")
        p = self.find_page
        online = self.online()
        paired = self.paired_keys()
        lost = self.cable_lost()
        self.lan_btn.set_visible(bool(lost))
        if self.needs_choice():
            title, desc = "Which device?", "Pick the phone or tablet to use this time."
        elif lost and (self.chosen in (None, lost)) and not self.connecting:
            title = f"{self.phone_name(lost)} needs its cable"
            desc = ("It switches Wi-Fi debugging off when the cable is pulled.\n"
                    "Fix once: plug it in, then on it open Developer options and turn on "
                    "<b>Allow ADB debugging in “Charge only” mode</b>.\n"
                    "Or skip adb: use the <b>Wi-Fi link</b> below.")
        elif self.chosen and self.chosen not in online and not self.tcp_addr(self.chosen) \
                and not self.connecting:
            title = f"Waiting for {self.phone_name(self.chosen)}"
            desc = ("Turn on <b>Wireless debugging</b> on it (same Wi-Fi), or plug in its cable.\n"
                    "Or pick another device below.")
        elif any(d["state"] == "unauthorized" for d in self.devs):
            title, desc = "Check your phone", "Tap “Allow” on the phone to let this laptop connect."
        elif any(self.errors.get(k) == "refused" for k in online):
            title = "Your phone refused to connect"
            desc = ("On the phone, turn <b>Wireless debugging</b> off and on again. "
                    "Still failing? Pair again or use a USB cable.")
        elif self.connecting:
            title, desc = "Connecting…", "Your phone was found on Wi-Fi."
        elif len([k for k in online if k in paired]) > 1:
            title, desc = "Choose a phone", "More than one paired phone is nearby. Pick one below."
        elif online and not any(k in paired for k in online):
            title, desc = "Phone found", "Pair it once — after that it connects by itself."
        elif self.cfg.get("manual_off") and online:
            title, desc = "Disconnected", "Press Connect next to your phone below."
        else:
            title = "Looking for your phone…"
            desc = ("Turn on <b>Wireless debugging</b> on the phone (same Wi-Fi).\n"
                    "First time? Pair it.")
        p.set_title(title)
        p.set_description(desc)

    # ================================================================ phones
    def online(self):
        """Paired-or-not phones advertising wireless debugging now: key -> ip:port."""
        return {phone_key(n): a for n, a in self.services.items()}

    def paired_keys(self):
        keys = set(self.cfg.get("phones", {}))
        try:
            with open(os.path.expanduser("~/.android/adb_known_hosts.pb"), "rb") as f:
                keys |= {phone_key(m.decode()) for m in re.findall(rb"adb-[0-9A-Za-z]+-[0-9A-Za-z]+", f.read())}
        except OSError:
            pass
        return keys - set(self.cfg.get("hidden", []))

    def phone_name(self, key):
        model = self.cfg.get("phones", {}).get(key, {}).get("model")
        return model or f"Android phone ({key[-4:]})"

    def remember_phone(self, key, model, tcp=None):
        phones = self.cfg.setdefault("phones", {})
        info = phones.setdefault(key, {})
        if model:
            info["model"] = model
        if tcp:
            info["tcp"] = tcp
        self.cfg["preferred"] = key
        hidden = self.cfg.get("hidden", [])
        if key in hidden:
            hidden.remove(key)
        save_cfg(self.cfg)

    def forget_phone(self, key):
        self.cfg.get("phones", {}).pop(key, None)
        self.cfg.setdefault("hidden", []).append(key)
        if self.cfg.get("preferred") == key:
            self.cfg.pop("preferred")
        save_cfg(self.cfg)
        self.phones_sig = None
        self.refresh()
        self.toast("Removed. To fully unpair: phone › Wireless debugging › Paired devices › Forget")

    def fill_phones(self):
        """(Re)build the phone list on the find page and in the Phones dialog."""
        online, paired = self.online(), self.paired_keys()
        keys = sorted(paired | set(online), key=lambda k: (k not in online, self.phone_name(k).lower()))
        cur = self.current_key()
        rows = []
        comp_key = self.comp_key()
        for k in keys:
            if k == comp_key:
                status = ("Connected · " if self.companion_serial() else "") + "Second screen"
            elif k == cur:
                status = "Connected"
            elif k == self.connecting:
                status = "Connecting…"
            elif self.errors.get(k) == "refused" and k in online:
                status = "Refused — turn Wireless debugging off and on"
            elif k in online:
                status = "Available on Wi-Fi" if k in paired else "Not paired yet"
            elif k == self.cable_lost():
                status = "Wi-Fi debugging off — plug in once, or use the Wi-Fi link"
            else:
                status = "Not nearby — turn on Wireless debugging"
            if self.needs_choice() and k != self.connecting:
                status = "Ready" if (k in online or self.tcp_addr(k) or self.usb_serial_of(k)) else status
            rows.append((k, self.phone_name(k), status,
                         k in online or bool(self.tcp_addr(k)) or bool(self.usb_serial_of(k)),
                         k in paired))
        sig = (tuple(rows), cur, comp_key)
        if sig == getattr(self, "phones_sig", None):
            return
        self.phones_sig = sig
        for grp in list(self.phone_groups):
            for r in getattr(grp, "rows", []):
                grp.remove(r)
            grp.rows = []
            grp.set_visible(bool(rows))
            for k, name, status, reachable, is_paired in rows:
                r = Adw.ActionRow(title=name, subtitle=status, use_markup=False)
                r.add_prefix(Gtk.Image(icon_name="phone-symbolic", pixel_size=24))
                if k == cur:
                    r.add_suffix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["accent"]))
                elif reachable and is_paired:
                    b = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
                    b.connect("clicked", lambda _b, key=k: self.switch_to(key))
                    r.add_suffix(b)
                elif k in online and not is_paired:
                    b = Gtk.Button(label="Pair", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
                    b.connect("clicked", lambda *_: self.open_pair())
                    r.add_suffix(b)
                if is_paired and k != cur and k != comp_key:
                    f = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                   tooltip_text="Remove from list", css_classes=["flat"])
                    f.connect("clicked", lambda _b, key=k: self.forget_phone(key))
                    r.add_suffix(f)
                grp.add(r)
                grp.rows.append(r)
        self.switch_btn.set_visible(len(rows) > 1)

    def open_phones(self):
        dlg = Adw.Dialog(title="Your phones", content_width=420, content_height=460)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(description="Paired phones. Connect to switch, or remove one.")
        page.add(grp)
        g2 = Adw.PreferencesGroup()
        add = Adw.ButtonRow(title="Pair a new phone", start_icon_name="list-add-symbolic")
        add.connect("activated", lambda *_: (dlg.close(), self.open_pair()))
        g2.add(add)
        page.add(g2)
        tv.set_content(page)
        dlg.set_child(tv)
        self.phone_groups.append(grp)
        dlg.connect("closed", lambda *_: self.phone_groups.remove(grp))
        self.phones_sig = None
        self.fill_phones()
        dlg.present(self)

    def current_key(self):
        s = self.serial()
        if not s:
            return None
        for k, a in self.online().items():
            if a == s:
                return k
        for k, info in self.cfg.get("phones", {}).items():
            if info.get("tcp") == s:
                return k
        return self.connected_key

    def cable_lost(self):
        """An old-Android device (Wi-Fi via adb tcpip) that is not reachable any more — usually because
        pulling the cable switched adb over Wi-Fi off (HarmonyOS/EMUI do this)."""
        online, cur = self.online(), self.current_key()
        keys = [self.chosen] if self.chosen else list(self.paired_keys())
        for k in keys:
            if k and self.tcp_addr(k) and k not in online and k != cur and not self.usb_serial_of(k) \
                    and self.errors.get(k) == "refused" and not self.lan_alive():
                return k
        return None

    def tcp_addr(self, key):
        return self.cfg.get("phones", {}).get(key, {}).get("tcp")

    # ================================================================ connection
    def on_service(self, name, ip, port):
        self.services[name] = f"{ip}:{port}"
        self.errors.pop(phone_key(name), None)
        self.try_connect(force=True)

    def on_service_gone(self, name):
        self.services.pop(name, None)

    def pick_target(self):
        online, paired = self.online(), self.paired_keys()
        pref = self.cfg.get("preferred")
        if pref in online or (pref and self.tcp_addr(pref) and pref not in online):
            return pref
        cands = [k for k in online if k in paired and k != self.comp_key()]
        if len(cands) == 1:
            return cands[0]
        if not cands and len(online) == 1:
            return next(iter(online))       # maybe paired under another name; worth a try
        return None                         # none, or several: let the user choose

    def try_connect(self, force=False, key=None):
        if self.main_serial() or self.connecting:
            return
        if not key and (self.cfg.get("manual_off") or (not force and time.time() - self.last_try < 6)):
            return
        if not key and self.needs_choice():
            self.refresh()
            return
        key = key or self.pick_target()
        if not key:
            self.refresh()
            return
        if self.usb_serial_of(key):
            self.connected_key = key
            self.remember_phone(key, None)
            self.poll_battery()
            self.refresh()
            self.ensure_companion()
            return
        addrs = [a for a in (self.online().get(key), self.tcp_addr(key)) if a]
        if not addrs:
            return
        self.last_try = time.time()
        self.connecting = key
        self.refresh()

        def work():
            for a in addrs:
                if connect_ok(adb("connect", a, timeout=8)):
                    devs = list_devices()
                    model = next((d["model"] for d in devs if d["serial"] == a), None)
                    return True, model, devs
                adb("disconnect", a, timeout=4)
            return False, None, list_devices()

        def done(res):
            ok, model, devs = res
            self.connecting = None
            self.devs = devs
            if ok:
                self.connected_key = key
                self.errors.pop(key, None)
                self.remember_phone(key, model)
                self.restore_screen_timeout()
                self.poll_battery()
            else:
                self.errors[key] = "refused" if key in self.paired_keys() else "unpaired"
            self.refresh()
        bg(work, done)

    def switch_to(self, key):
        self.cfg.pop("manual_off", None)
        first = self.chosen is None
        self.chosen = key
        self.phones_sig = None
        cur = None if first else self.main_serial()
        for name in list(self.tasks):
            self.stop_task(name)
        self.errors.pop(key, None)
        self.cfg["preferred"] = key
        save_cfg(self.cfg)

        def work():
            if cur:
                adb("disconnect", cur, timeout=4)
            return list_devices()

        def done(devs):
            self.devs = [d for d in devs if d["serial"] != cur]
            if self.main_serial():
                self.connected_key = key
                self.poll_battery()
            self.try_connect(force=True, key=key)
            self.ensure_companion()
            self.refresh()
        bg(work, done)

    def poll(self):
        if not os.path.exists(ADB):
            self.refresh()
            return True

        busy = any(self.running(n) for n in list(self.tasks))
        phones = {k: dict(v) for k, v in self.cfg.get("phones", {}).items()}

        def work():
            devs = list_devices()
            stale = [d["serial"] for d in devs if d["wifi"] and d["state"] == "offline"]
            for s in stale:              # old Wi-Fi port; the phone moved to a new one
                adb("disconnect", s, timeout=4)
            devs = [d for d in devs if d["serial"] not in stale]
            switched = [] if busy else self.auto_wifi(devs, phones)
            return (list_devices() if switched else devs), switched

        def done(res):
            devs, switched = res
            for key, addr, model in switched:     # remember the Wi-Fi address (don't change the preferred device)
                info = self.cfg.setdefault("phones", {}).setdefault(key, {})
                info["tcp"] = addr
                info.setdefault("model", model)
                save_cfg(self.cfg)
                self.toast(f"{model} is now on Wi-Fi too — unplug to try it")
            had = self.serial()
            self.devs = devs
            if had != self.serial():
                self.poll_battery()
            self.refresh()
            self.try_connect()
            self.ensure_companion()
        bg(work, done)
        return True

    def auto_wifi(self, devs, phones):
        """Android 10 and older have no wireless debugging: when such a device is plugged in, switch it to
        Wi-Fi (adb tcpip) right away so pulling the cable doesn't cut it off. Runs in the background."""
        tried = self.__dict__.setdefault("auto_wifi_tried", {})
        wifi = {d["serial"] for d in devs if d["wifi"] and d["state"] == "device"}
        out = []
        for d in devs:
            sn = d["serial"]
            if d["wifi"] or d["state"] != "device" or time.time() - tried.get(sn, 0) < 120:
                continue
            key = f"adb-{sn}"
            if phones.get(key, {}).get("tcp") in wifi:
                continue                       # already on Wi-Fi as well
            tried[sn] = time.time()
            try:
                ver = int(adb("-s", sn, "shell", "getprop", "ro.build.version.release", timeout=5).split(".")[0])
            except ValueError:
                continue
            if ver >= 11:
                continue                       # has wireless debugging; it reconnects by itself
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)",
                          adb("-s", sn, "shell", "ip", "-f", "inet", "addr", "show", "wlan0", timeout=8))
            if not m:
                continue
            addr = f"{m.group(1)}:5555"
            if not connect_ok(adb("connect", addr, timeout=6)):
                adb("-s", sn, "tcpip", "5555", timeout=10)
                for _ in range(8):
                    time.sleep(1)
                    if connect_ok(adb("connect", addr, timeout=6)):
                        break
                else:
                    continue
            out.append((key, addr, d["model"]))
        return out

    def ensure_companion(self):
        """Keep the companion (tablet) device connected alongside the phone."""
        addr, k = self.companion_addr(), self.comp_key()
        if not addr or self.needs_choice() or self.companion_serial() or getattr(self, "comp_connecting", False) \
                or self.cfg.get("manual_off"):
            return
        self.comp_connecting = True

        def done(out):
            self.comp_connecting = False
            if connect_ok(out):
                self.poll()
        bg(lambda: adb("connect", addr, timeout=8), done)

    def fill_screen_picker(self):
        """Keep the "Show on" list in sync: this device first, then the other paired ones."""
        cur = self.current_key()
        others = sorted((k for k in self.paired_keys() if k != cur), key=lambda k: self.phone_name(k).lower())
        keys = ([cur] if cur else []) + others
        labels = [self.phone_name(k) + (" (this one)" if k == cur and others else "") for k in keys]
        target = self.comp_key() if self.comp_key() in keys else cur
        sig = (tuple(keys), tuple(labels), target)
        if sig != getattr(self, "screen_sig", None):
            self.screen_sig = sig
            self.screen_dd.handler_block(self.screen_dd_h)
            self.screen_keys = keys
            self.screen_model.splice(0, self.screen_model.get_n_items(), labels)
            if target in keys:
                self.screen_dd.set_selected(keys.index(target))
            self.screen_dd.handler_unblock(self.screen_dd_h)
        k = self.comp_key()
        if k and k != cur and not self.companion_serial() and not self.lan_alive():
            self.screen_hint.set_label(f"{self.phone_name(k)} isn't connected. Open the Wi-Fi link page on it "
                                       "(see Wi-Fi link), or turn on its debugging.")
            self.screen_hint.set_visible(True)
        else:
            self.screen_hint.set_visible(False)

    def on_screen_pick(self, dd, _pspec):
        i = dd.get_selected()
        if i >= len(self.screen_keys):
            return
        key = self.screen_keys[i]
        if key == self.current_key():
            self.cfg.pop("companion", None)
        else:
            self.cfg["companion"] = key
        save_cfg(self.cfg)
        self.phones_sig = None
        self.ensure_companion()
        self.refresh()

    def poll_battery(self):
        s = self.serial()
        if s:
            def work():
                out = adb("-s", s, "shell", "dumpsys battery; echo ANDROID=$(getprop ro.build.version.release)",
                          timeout=6)
                m = re.search(r"^\s*level:\s*(\d+)", out, re.M)
                st = re.search(r"^\s*status:\s*(\d+)", out, re.M)
                ver = re.search(r"ANDROID=(\S+)", out)
                return (int(m.group(1)) if m else None, bool(st and st.group(1) in ("2", "5")),
                        ver.group(1) if ver else None)

            def done(res):
                self.battery, self.charging, self.android = res
                self.refresh()
            bg(work, done)
        return True

    def after_pair(self):
        self.errors.clear()
        self.cfg.pop("manual_off", None)
        self.cfg.pop("preferred", None)     # connect to the phone that was just paired
        save_cfg(self.cfg)
        self.toast("Phone paired")
        self.phones_sig = None
        self.poll()
        GLib.timeout_add_seconds(2, lambda: (self.try_connect(force=True), False)[1])

    def search_again(self):
        self.cfg.pop("manual_off", None)
        self.errors.clear()
        self.avahi.stop(self.connect_browser)
        self.services.clear()
        self.connect_browser = self.avahi.browse(CONNECT_T, self.on_service, self.on_service_gone)
        self.toast("Searching…")
        self.poll()

    def disconnect(self):
        s = self.serial()
        for name in list(self.tasks):
            self.stop_task(name)
        self.cfg["manual_off"] = True   # don't auto-reconnect until the user picks a phone
        if s:
            bg(lambda: adb("disconnect", s), lambda _o: self.poll())
        self.devs = []
        self.refresh()

    def open_pair(self, tab="qr"):
        PairDialog(self, tab).present(self)

    # ---- keep the phone screen on while mirroring (restored afterwards)
    def restore_screen_timeout(self):
        s, saved = self.serial(), self.cfg.get("saved_timeout")
        if not s or saved is None or self.running("mirror"):
            return
        self.cfg.pop("saved_timeout")
        save_cfg(self.cfg)
        bg(lambda: adb("-s", s, "shell", "settings", "put", "system", "screen_off_timeout", str(saved),
                       timeout=6))

    def install_scrcpy(self, *_):
        self.install_btn.set_sensitive(False)
        pkgs = ["scrcpy"] + ([] if os.path.exists(ADB) else ["adb"])

        def done(rc):
            global ADB
            ADB = ADB if os.path.exists(ADB) else (shutil.which("adb") or ADB)
            self.install_btn.set_sensitive(True)
            self.toast("Installed" if rc == 0 else "Install cancelled or failed")
            self.poll()
        bg(lambda: subprocess.call(["pkexec", "apt-get", "install", "-y", *pkgs]), done)

    # ================================================================ scrcpy sessions
    def android_ver(self):
        try:
            return int(str(self.android).split(".")[0])
        except (TypeError, ValueError):
            return None

    def has_audio(self):
        """Sound capture needs Android 11+."""
        v = self.android_ver()
        return v is None or v >= 11

    def has_camera(self):
        """Camera capture needs Android 12+."""
        v = self.android_ver()
        return v is None or v >= 12

    def base_cmd(self):
        return ["scrcpy", "-s", self.serial()]

    def window_opts(self):
        """Options shared by every window that shows the phone (mirror and app windows)."""
        o = self.opt
        cmd = [["--max-size=1024", "--video-bit-rate=4M"], ["--max-size=1600"], []][o("size")]
        cmd.append(f"--max-fps={[30, 60, 120][o('fps')]}")
        cmd += [[], ["--audio-source=playback", "--audio-dup"], ["--no-audio"]][o("audio_out")] \
            if self.has_audio() else ["--no-audio"]
        if o("view_only"):
            cmd.append("--no-control")
        elif o("uhid_kb"):
            cmd.append("--keyboard=uhid")
        if not o("clipboard"):
            cmd.append("--no-clipboard-autosync")
        if o("on_top"):
            cmd.append("--always-on-top")
        if o("borderless"):
            cmd.append("--window-borderless")
        return cmd

    def mirror_cmd(self):
        o = self.opt
        cmd = self.base_cmd() + ["--window-title", self.h_title.get_label() or "Phone"] + self.window_opts()
        mode = o("phone_mode")
        if mode == 0:
            cmd.append("--turn-screen-off")
        if mode in (0, 1):                       # never sleep while mirroring; restored on exit
            cmd += [f"--screen-off-timeout={KEEP_ON_S}", "--stay-awake"]
        if not o("wake_on_start") and mode == 2:
            cmd.append("--no-power-on")
        if o("lock_on_close"):
            cmd.append("--power-off-on-close")
        if o("fullscreen"):
            cmd.append("--fullscreen")
        return cmd

    def record_cmd(self):
        os.makedirs(VIDS, exist_ok=True)
        self.record_path = os.path.join(VIDS, time.strftime("Phone-%Y%m%d-%H%M%S.mp4"))
        cmd = self.base_cmd() + ["--no-window", "--max-size=1920",   # needs the screen awake to record
                                 f"--record={self.record_path}"]
        # Record phone sound without silencing the phone; or record video only.
        return cmd + (["--audio-source=playback", "--audio-dup", "--no-audio-playback"]
                      if self.opt("rec_audio") and self.has_audio() else ["--no-audio"])

    def toggle(self, name, make_cmd):
        if self.running(name):
            self.stop_task(name)
            if name == "mirror":                 # hiding the phone also closes its app windows
                for n in [n for n in self.tasks if n.startswith("app:")]:
                    self.stop_task(n)
        elif self.serial():
            self.start_task(name, make_cmd())

    def start_task(self, name, cmd, stdin=None):
        try:
            p = subprocess.Popen(cmd, env=dict(os.environ, ADB=ADB), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=stdin is None, stdin=stdin)
        except Exception as e:
            self.toast(f"Could not start: {e}")
            return
        self.tasks[name] = p
        if name == "mirror":
            self.mirror_started = time.time()
        self.refresh()

        def watch():
            err = p.stderr.read()
            if isinstance(err, bytes):
                err = err.decode(errors="replace")
            return p.wait(), err

        def done(res):
            rc, err = res
            if self.tasks.get(name) is p:
                self.tasks.pop(name, None)
            if name == "webcam" and getattr(self, "comp_srv", None):
                self.comp_srv.end_stream("cam")
            if name == "mic":
                self.stop_mic_feed()
            if name == "speaker":
                self.stop_speaker_feed()
            if name == "mirror":
                self.restore_screen_timeout()
            if name == "record" and os.path.exists(getattr(self, "record_path", "")):
                self.toast("Recording saved", self.record_path)
            elif rc not in (0, -2, -15, 2):
                lines = [l for l in err.splitlines() if "ERROR" in l] or err.splitlines()[-1:]
                hint = ""
                if "inject" in err.lower():
                    hint = " — turn on “USB debugging (Security settings)” in Developer options"
                self.toast((lines[-1].replace("ERROR: ", "") if lines else "Stopped") + hint)
            self.refresh()
        bg(watch, done)

    def stop_task(self, name):
        if self.running(name):
            p = self.tasks[name]
            if p.stdin:
                try:
                    p.stdin.close()
                except OSError:
                    pass
            p.send_signal(signal.SIGINT)

    # ================================================================ features
    # ---- companion screen (tablet)
    def companion_actions(self):
        return {
            "phone_state": lambda: {"model": self.h_title.get_label() if self.main_serial() else None,
                                    "battery": self.battery, "charging": self.charging},
            "phone_screen": lambda: GLib.idle_add(lambda: (self.toggle("mirror", self.mirror_cmd), False)[1]),
        }

    def lan_alive(self):
        srv = getattr(self, "comp_srv", None)
        return bool(srv and srv.lan_server and srv.lan_alive())

    def lan_setup(self):
        """Wi-Fi link: the tablet page over HTTPS on the local network, so no cable is needed after restarts."""
        if self.cfg.get("lan_mode") and getattr(self, "comp_srv", None) and self.comp_srv.lan_server:
            self.lan_info()
            return
        if not self.ensure_server():
            return
        subnet = companion.lan_subnet()
        ufw = "/usr/sbin/ufw"
        need_fw = os.path.exists(ufw) and not self.cfg.get("lan_fw") and subnet

        def work():
            if need_fw:
                rc = subprocess.call(["pkexec", "sh", "-c",
                                      f"{ufw} allow from {subnet} to any port {companion.LAN_PORT} proto tcp "
                                      f"comment 'Phone Connect Wi-Fi link' && "
                                      f"{ufw} allow from {subnet} to any port {display.PORT} proto tcp "
                                      f"comment 'Phone Connect extra display'"])
                if rc:
                    return "Firewall not changed (cancelled) — the tablet can't reach the laptop yet"
            try:
                self.comp_srv.start_lan()
            except OSError as e:
                return str(e)
            return None

        def done(err):
            if err and "Firewall" not in err:
                self.toast(err)
                return
            if need_fw and not err:
                self.cfg["lan_fw"] = True
            self.cfg["lan_mode"] = True
            save_cfg(self.cfg)
            if err:
                self.toast(err)
            self.lan_info()
            self.refresh()
        if need_fw:
            self.toast("Allowing the tablet through the firewall (local network only) — enter your password")
        bg(work, done)

    def lan_info(self):
        url = self.comp_srv.lan_url()
        dlg = Adw.AlertDialog(heading="Wi-Fi link is on",
                              body="On the tablet (same Wi-Fi), once:\n"
                                   "1. Scan this code with the camera, or open the link in the browser\n"
                                   "2. “Not private” warning: tap Advanced › Proceed (it's your laptop)\n"
                                   "3. Browser menu › Add to home screen\n\n"
                                   "Then open it from the home screen anytime — even after a restart, no cable.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        try:
            png = subprocess.run(["qrencode", "-t", "PNG", "-s", "6", "-m", "2", "-o", "-", url],
                                 capture_output=True, timeout=5).stdout
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            box.append(Gtk.Picture(paintable=tex, can_shrink=False, halign=Gtk.Align.CENTER))
        except Exception:  # noqa: BLE001
            pass
        row = Adw.ActionRow(title="Link", subtitle=url, use_markup=False, subtitle_selectable=True,
                            css_classes=["card"])
        cp = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        cp.connect("clicked", lambda *_: (self.get_clipboard().set(url), self.toast("Copied")))
        row.add_suffix(cp)
        box.append(row)
        dlg.set_extra_child(box)
        dlg.add_response("off", "Turn off")
        dlg.set_response_appearance("off", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.add_response("ok", "Done")
        dlg.set_default_response("ok")

        def resp(_d, r):
            if r == "off":
                self.comp_srv.stop_lan()
                self.cfg["lan_mode"] = False
                save_cfg(self.cfg)
                self.toast("Wi-Fi link off")
                self.refresh()
        dlg.connect("response", resp)
        dlg.present(self)

    def ensure_server(self):
        if not getattr(self, "comp_srv", None):
            self.comp_srv = companion.CompanionServer(self.companion_actions())
            self.comp_srv.cam_facing = ["back", "front"][self.cfg.get("facing", 0)]
            self.comp_srv.on_facing = lambda f: GLib.idle_add(
                lambda: (self.set_opt("facing", 0 if f == "back" else 1), self.refresh(), False)[2])
        try:
            self.comp_srv.start()
            return True
        except OSError as e:
            self.toast(f"Could not start the page server: {e}")
            return False

    # ---- camera & mic through the device's browser (works on Android 10/11 too)
    def open_media_page(self, s):
        """Open the camera/mic page on the device unless it is already open."""
        srv = self.comp_srv
        if self.lan_alive():                          # Wi-Fi link page: ask it to show Camera & audio
            srv.ask_view("media")
            return
        if not s:
            return
        if (time.time() - srv.media_seen < 6 or srv.media_connected("cam") or srv.media_connected("mic")
                or time.time() - getattr(self, "media_opened", 0) < 20):
            return                                   # already open (or opening) there
        self.media_opened = time.time()
        url = self.comp_srv.url("media") + "&facing=" + self.comp_srv.cam_facing
        bg(lambda: companion.open_on_device(ADB, s, url),
           lambda ok: None if ok else self.toast("Could not open the browser on the device"))

    def browser_webcam(self, dev):
        s = self.serial()
        if (not s and not self.lan_alive()) or not self.ensure_server():
            return
        self.comp_srv.cam_facing = ["back", "front"][self.cfg.get("facing", 0)]
        self.start_task("webcam", ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
                                   "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0",
                                   "-use_wallclock_as_timestamps", "1", "-f", "mjpeg", "-i", "-",
                                   "-fps_mode", "passthrough",
                                   "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                                          "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                                   "-f", "v4l2", dev], stdin=subprocess.PIPE)
        p = self.tasks.get("webcam")
        if not p:
            return

        def feed(data):
            p.stdin.write(data)
            p.stdin.flush()
        self.comp_srv.sinks["cam"] = feed
        self.open_media_page(s)
        self.toast("Webcam on — tap Allow on the device, then choose “Phone Camera” in your video app")

    def toggle_mic(self):
        if self.running("mic"):
            self.stop_task("mic")
            if getattr(self, "comp_srv", None):
                self.comp_srv.end_stream("mic")
            self.toast("Microphone off")
            return
        s = self.serial()
        if not s and not self.lan_alive():
            self.toast("Connect a phone or tablet first")
            return
        if not shutil.which("pw-cli") or not shutil.which("pw-cat") or not shutil.which("pw-link"):
            self.toast("PipeWire tools are missing (pipewire-bin)")
            return
        if not self.ensure_server():
            return
        name = re.sub(r'["{}\\]', "", (self.h_title.get_label() or "Phone") + " mic")
        # A virtual microphone that lives as long as this pw-cli process (object.linger=false).
        self.start_task("mic", ["pw-cli"], stdin=subprocess.PIPE)
        p = self.tasks.get("mic")
        if not p:
            return
        try:
            p.stdin.write(("create-node adapter { factory.name=support.null-audio-sink node.name=phone_connect_mic "
                           f'node.description="{name}" media.class=Audio/Source/Virtual audio.position=[MONO] '
                           "object.linger=false }\n").encode())
            p.stdin.flush()
        except OSError:
            self.toast("Could not create the microphone")
            return

        def link(feed):
            for _ in range(20):                    # wait for both nodes, then connect them
                time.sleep(0.25)
                if feed.poll() is not None:
                    return
                r = subprocess.run(["pw-link", "phone_connect_mic_feed:output_MONO", "phone_connect_mic:input_MONO"],
                                   capture_output=True, text=True)
                if r.returncode == 0 or "exists" in r.stderr.lower():
                    return

        def on_data(data):
            if isinstance(data, dict):           # {"rate": 48000} from the page
                rate = int(data.get("rate") or 48000)
                self.stop_mic_feed()
                self.mic_feed = subprocess.Popen(
                    ["pw-cat", "--playback", "--raw", "--volume", "1", "-P",
                     "{ node.name=phone_connect_mic_feed node.autoconnect=false state.restore-props=false }",
                     "--rate", str(rate), "--channels", "1", "--format", "s16", "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                threading.Thread(target=link, args=(self.mic_feed,), daemon=True).start()
                return
            f = getattr(self, "mic_feed", None)
            if f and f.poll() is None:
                f.stdin.write(data)
                f.stdin.flush()
        self.comp_srv.sinks["mic"] = on_data
        self.open_media_page(s)
        self.toast(f"Microphone on — tap Allow on the device, then pick “{name}” as your input")
        self.refresh()

    # ---- device as a speaker for the laptop (browser playback; any Android version)
    @staticmethod
    def pw_node_id(name):
        try:
            out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
            for o in json.loads(out):
                props = (o.get("info") or {}).get("props") or {}
                if o.get("type", "").endswith("Node") and props.get("node.name") == name:
                    return o["id"]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return None

    @staticmethod
    def default_sink_name():
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout
        m = re.search(r'node\.name = "([^"]+)"', out)
        return m.group(1) if m else None

    def toggle_speaker(self):
        if self.running("speaker"):
            self.stop_task("speaker")
            if getattr(self, "comp_srv", None):
                self.comp_srv.end_stream("spk")
            self.toast("Speaker off — sound is back on the laptop")
            return
        s = self.serial()
        if not s and not self.lan_alive():
            self.toast("Connect a phone or tablet first")
            return
        if not all(shutil.which(t) for t in ("pw-cli", "pw-record", "pw-link", "wpctl", "pw-dump")):
            self.toast("PipeWire tools are missing (pipewire-bin, wireplumber)")
            return
        if not self.ensure_server():
            return
        name = re.sub(r'["{}\\]', "", (self.h_title.get_label() or "Phone") + " speaker")
        self.start_task("speaker", ["pw-cli"], stdin=subprocess.PIPE)
        p = self.tasks.get("speaker")
        if not p:
            return
        try:
            p.stdin.write(("create-node adapter { factory.name=support.null-audio-sink node.name=phone_connect_speaker "
                           f'node.description="{name}" media.class=Audio/Sink audio.position=[FL,FR] '
                           "object.linger=false }\n").encode())
            p.stdin.flush()
        except OSError:
            self.toast("Could not create the speaker")
            return
        srv = self.comp_srv
        srv.sinks["spk"] = lambda _d: None          # accept the page's connection

        def work():
            sink_id = None
            for _ in range(20):
                time.sleep(0.25)
                sink_id = self.pw_node_id("phone_connect_speaker")
                if sink_id:
                    break
            if not sink_id:
                return "Could not create the speaker"
            prev = self.default_sink_name()
            if prev and prev != "phone_connect_speaker":
                self.speaker_prev_default = prev
            subprocess.run(["wpctl", "set-default", str(sink_id)], capture_output=True)
            rec = subprocess.Popen(["pw-record", "--raw", "-P",
                                    "{ node.name=phone_connect_speaker_tap node.autoconnect=false "
                                    "state.restore-props=false }", "--volume", "1",
                                    "--rate", "48000", "--channels", "2", "--format", "s16", "-"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.speaker_feed = rec
            for _ in range(20):
                time.sleep(0.25)
                ok = all(subprocess.run(["pw-link", f"phone_connect_speaker:monitor_{c}",
                                         f"phone_connect_speaker_tap:input_{c}"],
                                        capture_output=True).returncode == 0 for c in ("FL", "FR"))
                if ok:
                    break

            def pump():                              # laptop sound -> page, 20 ms chunks
                while True:
                    chunk = rec.stdout.read(3840)
                    if not chunk:
                        break
                    srv.broadcast("spk", chunk)
            threading.Thread(target=pump, daemon=True).start()
            return None

        def done(err):
            if err:
                self.toast(err)
                self.stop_task("speaker")
            self.refresh()
        bg(work, done)
        self.open_media_page(s)
        self.toast(f"Speaker on — laptop sound now plays on the device (tap “Tap to turn on sound” there once)")
        self.refresh()

    def stop_speaker_feed(self, wait=False):
        f = getattr(self, "speaker_feed", None)
        self.speaker_feed = None
        if f and f.poll() is None:
            f.terminate()
        prev = getattr(self, "speaker_prev_default", None)
        self.speaker_prev_default = None
        if prev:                                     # give the laptop its own output back
            def restore():
                for _ in range(10):
                    i = self.pw_node_id(prev)
                    if i:
                        subprocess.run(["wpctl", "set-default", str(i)], capture_output=True)
                        return
                    time.sleep(0.3)
            if wait:
                restore()
            else:
                threading.Thread(target=restore, daemon=True).start()

    def stop_mic_feed(self):
        f = getattr(self, "mic_feed", None)
        self.mic_feed = None
        if f and f.poll() is None:
            try:
                f.stdin.close()
            except OSError:
                pass
            f.terminate()

    def companion_open(self, view):
        adb_target = self.companion_serial() if self.comp_key() else self.serial()
        if not adb_target and self.lan_alive():      # tablet on the Wi-Fi link
            if view == "draw" and not companion.uinput_ready():
                pass                                 # fall through to the one-time setup below
            else:
                self.comp_srv.ask_view(view)
                name = {"panel": "Control panel", "monitor": "Live monitor", "draw": "Drawing pad"}[view]
                self.toast(f"{name} is showing on {self.comp_srv.lan_device or 'the tablet'} (Wi-Fi)")
                return
        if self.comp_key() and not self.companion_serial() and not self.lan_alive():
            self.toast(f"{self.phone_name(self.comp_key())} isn't connected — pick another device under “Show on”")
            self.ensure_companion()
            return
        s = self.companion_serial() or self.serial()
        if not s and not self.lan_alive():
            self.toast("Connect a phone or tablet first")
            return
        if view == "draw" and not companion.uinput_ready():
            self.toast("One-time setup for the drawing pad — enter your password")

            def setup_done(rc):
                if companion.uinput_ready():
                    self.toast("Drawing pad ready")
                    self.companion_open("draw")
                else:
                    self.toast("Drawing pad setup cancelled or failed")
                self.refresh()
            bg(lambda: subprocess.call(["pkexec", "sh", "-c", companion.UINPUT_SETUP]), setup_done)
            return
        if not self.ensure_server():
            return
        name = {"panel": "Control panel", "monitor": "Live monitor", "draw": "Drawing pad"}[view]
        dev = self.phone_name(self.comp_key()) if self.companion_serial() else (self.phone_name(self.current_key())
                                                                               if self.current_key() else "the device")
        self.toast(f"Opening {name} on {dev}…")
        bg(lambda: companion.open_on_device(ADB, s, self.comp_srv.url(view)),
           lambda ok: self.toast(f"{name} is open on {dev}" if ok else f"Could not open the browser on {dev}"))

    # ---- extra display (tablet as a monitor)
    def screen_device(self):
        """adb serial of the device picked under "Show on" (or the connected one)."""
        if self.comp_key():
            return self.companion_serial()
        return self.serial()

    def extra_display(self):
        if self.extend_on:
            self.extra_display_info(on=True)
            return
        s = self.screen_device()
        if not s and not self.lan_alive():
            name = self.phone_name(self.comp_key()) if self.comp_key() else "a device"
            self.toast(f"Connect {name} first")
            return
        if not display.available():
            self.toast("GNOME Remote Desktop is missing — install gnome-remote-desktop")
            return
        self.toast("Turning on the extra display…")

        def work():
            if s and not display.has_client(ADB, s):
                return "noapp"
            try:
                return "ok" if display.start(ADB, s) else "noapp"
            except Exception as e:  # noqa: BLE001
                return str(e)

        def done(res):
            if res == "ok":
                self.extend_on, self.extend_serial = True, s
                self.extend_pc = "localhost" if s else (companion.lan_ip() or "this laptop's IP")
                self.extra_display_info(on=True, first=True)
            elif res == "noapp":
                self.toast("Install “Remote Desktop” by Microsoft on the tablet first (AppGallery or Play Store)")
            else:
                display.stop(ADB, s)
                self.toast(res)
            self.refresh()
        bg(work, done)

    def stop_extra_display(self):
        s, self.extend_on, self.extend_serial = self.extend_serial, False, None
        bg(lambda: display.stop(ADB, s), lambda _r: (self.toast("Extra display off"), self.refresh()))

    def extra_display_info(self, on=True, first=False):
        user, pw = display.login()
        dlg = Adw.AlertDialog(heading="Extra display is on",
                              body="On the tablet, in Remote Desktop:\n"
                                   "1. Tap + › Add PC (first time only)\n"
                                   "2. PC name: as shown below\n"
                                   "3. User account › Add: the user and password below\n"
                                   "4. Tap the PC to connect\n\n"
                                   "It appears in Settings › Displays — arrange it like any monitor.")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for title, value in (("PC name", getattr(self, "extend_pc", "localhost")), ("User", user), ("Password", pw)):
            r = Adw.ActionRow(title=title, subtitle=value, use_markup=False, subtitle_selectable=True)
            b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Copy")
            b.connect("clicked", lambda _b, v=value: (self.get_clipboard().set(v), self.toast("Copied")))
            r.add_suffix(b)
            box.append(r)
        dlg.set_extra_child(box)
        dlg.add_response("off", "Turn off")
        dlg.set_response_appearance("off", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.add_response("ok", "Done")
        dlg.set_default_response("ok")
        dlg.connect("response", lambda _d, r: self.stop_extra_display() if r == "off" else None)
        dlg.present(self)

    def open_display_settings(self):
        subprocess.Popen(["gnome-control-center", "display"])

    # ---- phone buttons
    def keyevent(self, code):
        s = self.serial()
        if s:
            bg(lambda: adb("-s", s, "shell", "input", "keyevent", code, timeout=6),
               lambda _o: self.check_wake())

    def wake_unlock(self):
        """Wake the phone; if it is locked, swipe up so the PIN pad shows in the phone window."""
        s = self.serial()
        if not s:
            return

        def work():
            adb("-s", s, "shell", "input", "keyevent", "KEYCODE_WAKEUP", timeout=6)
            time.sleep(0.7)
            out = adb("-s", s, "shell", "dumpsys window | grep -m1 isKeyguardShowing", timeout=6)
            if "isKeyguardShowing=true" not in out:
                return False
            m = re.search(r"(\d+)x(\d+)", adb("-s", s, "shell", "wm", "size", timeout=6))
            w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 2340)
            adb("-s", s, "shell", "input", "swipe", str(w // 2), str(int(h * 0.8)), str(w // 2),
                str(int(h * 0.3)), "250", timeout=6)
            return True

        def done(locked):
            if locked and self.running("mirror"):
                self.toast("Click the phone window and type your PIN, then press Enter")
            elif locked:
                self.toast("Phone is awake and showing the PIN screen — or press Show phone screen to type it here")
            else:
                self.toast("Phone is awake")
            self.check_wake()
        bg(work, done)

    def check_wake(self):
        """While mirroring, react to the phone being locked/asleep (its window is then black):
        close the phone window (default) or show a Wake banner."""
        s = self.serial()
        if not s or not self.running("mirror"):
            self.sleep_banner.set_revealed(False)
            self.asleep_count = 0
            return True
        if time.time() - getattr(self, "mirror_started", 0) < 6:     # let the phone wake up first
            return True
        started = self.mirror_started

        def done(out):
            if started != getattr(self, "mirror_started", 0) or not self.running("mirror"):
                return
            asleep = "Asleep" in out or "Dozing" in out
            self.asleep_count = getattr(self, "asleep_count", 0) + 1 if asleep else 0
            if asleep and self.opt("on_lock") == 0 and self.asleep_count >= 2:   # ~6 s, ignores blips
                self.asleep_count = 0
                self.sleep_banner.set_revealed(False)
                self.toggle("mirror", self.mirror_cmd)        # closes the phone + app windows
                self.toast("Phone locked — phone window closed")
            else:
                self.sleep_banner.set_revealed(asleep and self.opt("on_lock") == 1)
        bg(lambda: adb("-s", s, "shell", "dumpsys power | grep -m1 mWakefulness=", timeout=5), done)
        return True

    def pick_app(self):
        if not self.serial():
            return
        if self.apps:
            AppPicker(self, self.apps).present(self)
            return
        self.toast("Loading apps…")
        s = self.serial()

        def work():
            try:
                out = subprocess.run(["scrcpy", "-s", s, "--list-apps"], capture_output=True, text=True,
                                     timeout=40, env=dict(os.environ, ADB=ADB)).stdout
            except Exception:
                return []
            apps = []
            for line in out.splitlines():
                m = re.match(r"^\s*[*-]\s+(.+?)\s{2,}(\S+)\s*$", line)
                if m:
                    apps.append((m.group(1).strip(), m.group(2)))
            return sorted(apps, key=lambda a: a[0].lower())

        def done(apps):
            if not apps:
                self.toast("Could not load the app list")
                return
            self.apps = apps
            AppPicker(self, apps).present(self)
        bg(work, done)

    def open_app(self, name, pkg):
        cmd = self.base_cmd() + ["--new-display", f"--start-app={pkg}", "--window-title", name,
                                 "--no-vd-system-decorations"] + self.window_opts()
        self.start_task(f"app:{pkg}:{time.time()}", cmd)
        self.toast(f"Opening {name}…")

    def screenshot(self):
        s = self.serial()
        os.makedirs(PICS, exist_ok=True)
        path = os.path.join(PICS, time.strftime("Screenshot-%Y%m%d-%H%M%S.png"))

        def work():
            try:
                with open(path, "wb") as f:
                    rc = subprocess.run([ADB, "-s", s, "exec-out", "screencap", "-p"], stdout=f,
                                        timeout=20).returncode
                return rc == 0 and os.path.getsize(path) > 0
            except Exception:
                return False
        bg(work, lambda ok: self.toast("Screenshot saved", path) if ok else self.toast("Screenshot failed"))

    def webcam(self):
        if self.running("webcam"):
            self.stop_task("webcam")
            return
        dev = webcam_device()
        if not dev:
            self.t_cam.set_sensitive(False)
            self.toast("Setting up the webcam (one time) — enter your password")

            def done(rc):
                self.t_cam.set_sensitive(True)
                self.toast("Webcam ready — tap it again to start" if webcam_device()
                           else "Webcam setup failed or cancelled")
                self.refresh()
            bg(lambda: subprocess.call(["pkexec", "sh", "-c", WEBCAM_SETUP]), done)
            return
        if self.lan_alive() or not self.has_camera():
            self.browser_webcam(dev)
            return
        facing = ["back", "front"][self.cfg.get("facing", 0)]
        self.start_task("webcam", self.base_cmd() + [
            "--video-source=camera", f"--camera-facing={facing}", "--camera-ar=16:9", f"--max-size={[1280, 1920][self.opt('cam_res')]}",
            "--no-audio", "--no-window", f"--v4l2-sink={dev}"])
        self.toast("Webcam on — choose “Phone Camera” in your video app")

    def flip_camera(self):
        if not self.has_camera():
            self.set_opt("facing", 1 - self.cfg.get("facing", 0))
            if getattr(self, "comp_srv", None):
                self.comp_srv.cam_facing = ["back", "front"][self.cfg.get("facing", 0)]
                sink = self.comp_srv.sinks.get("cam")
                if sink:                       # page reconnects with the other camera
                    self.comp_srv.end_stream("cam")
                    self.comp_srv.sinks["cam"] = sink
            self.refresh()
            return
        self.set_opt("facing", 1 - self.cfg.get("facing", 0))
        if self.running("webcam"):      # restart with the other camera
            p = self.tasks["webcam"]
            self.stop_task("webcam")

            def restart():
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
            bg(restart, lambda _r: self.webcam())
        self.refresh()

    def choose_files(self):
        dlg = Gtk.FileDialog(title="Send to phone")

        def chosen(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.send_files([f.get_path() for f in files if f.get_path()])
        dlg.open_multiple(self, None, chosen)

    def on_drop(self, _t, value, _x, _y):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not self.serial():
            self.toast("Connect your phone first")
            return False
        self.send_files(paths)
        return True

    def send_files(self, paths):
        s = self.serial()
        if not paths or not s:
            return
        self.toast(f"Sending {len(paths)} file(s)…")

        def work():
            ok = 0
            for p in paths:
                if "pushed" in adb("-s", s, "push", p, "/sdcard/Download/", timeout=600):
                    ok += 1
                    name = os.path.basename(p.rstrip("/"))
                    adb("-s", s, "shell", "am", "broadcast", "-a",
                        "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                        "-d", f"file:///sdcard/Download/{name}", timeout=5)
            return ok
        bg(work, lambda ok: self.toast(f"Sent {ok} of {len(paths)} to the phone’s Download folder"))

    def browse_files(self):
        if self.serial():
            FileBrowser(self).present(self)

    def gsconnect(self):
        state = gsconnect_state()
        if state == "ready":
            subprocess.Popen(["gnome-extensions", "prefs", GSCONNECT])
            self.toast("Install “KDE Connect” on the phone, then pair it in this window")
        elif state == "needs-login":
            self.toast("Turned on — log out and back in once to start it. Turn off in Settings › General")
        elif state == "off":
            self.set_notifications(True)
        else:
            self.t_notif.set_sensitive(False)
            self.toast("Installing GSConnect — enter your password")

            def done(rc):
                self.t_notif.set_sensitive(True)
                if rc == 0:
                    set_gsconnect(True)
                    self.gsc = gsconnect_state()
                    self.toast("Installed — log out and back in once, then install “KDE Connect” on the phone")
                else:
                    self.toast("GSConnect install cancelled or failed")
                self.refresh()
            bg(lambda: subprocess.call(["pkexec", "apt-get", "install", "-y",
                                        "gnome-shell-extension-gsconnect"]), done)

    def set_notifications(self, on):
        set_gsconnect(on)
        self.gsc = gsconnect_state()
        if on:
            self.toast("Phone notifications turned on" + (" — log out and back in once to start them"
                                                           if self.gsc == "needs-login" else ""))
        else:
            self.toast("Phone notifications turned off")
        self.refresh()

    def on_close(self, *_):
        """Closing the app turns everything off; the window hides at once while that finishes."""
        if getattr(self, "closing", False):
            return True
        self.closing = True
        self.set_visible(False)
        bg(self.shutdown, lambda _r: self.destroy())
        return True

    def shutdown(self):
        """Stop every phone session, give the phone its settings back and disconnect it."""
        # 1. mirror, app windows, webcam, recording: ask nicely (recordings finish saving), then force
        procs = [p for p in self.tasks.values() if p.poll() is None]
        for p in procs:
            p.send_signal(signal.SIGINT)
        deadline = time.time() + 6
        for p in procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
        for p in subprocess.run(["pgrep", "-f", f"scrcpy -s {self.serial()}"], capture_output=True,
                                text=True).stdout.split() if self.serial() else []:
            try:
                os.kill(int(p), signal.SIGTERM)  # any session started here that slipped through
            except (OSError, ValueError):
                pass
        # 2. phone screen timeout back to normal
        saved, s = self.cfg.pop("saved_timeout", None), self.serial()
        if saved is not None and s:
            adb("-s", s, "shell", "settings", "put", "system", "screen_off_timeout", str(saved), timeout=5)
        save_cfg(self.cfg)
        self.stop_mic_feed()
        self.stop_speaker_feed(wait=True)
        # 3. extra display off (Remote Desktop back to how it was)
        if self.extend_on:
            try:
                display.stop(ADB, self.extend_serial)
            except Exception:  # noqa: BLE001
                pass
        # 3b. companion screen: stop the local server and remove the tunnels on devices
        if getattr(self, "comp_srv", None):
            self.comp_srv.stop()
        for d in self.devs:
            if d["state"] == "device":
                try:
                    companion.close_on_device(ADB, d["serial"])
                except Exception:  # noqa: BLE001
                    pass
        # 4. stop Wi-Fi discovery and disconnect the phone
        GLib.idle_add(lambda: self.avahi.stop(self.connect_browser) and False)
        adb("disconnect", timeout=5)    # every Wi-Fi connection
        for d in list_devices(raw=True):  # incl. ones adb made by itself (mDNS names)
            if d["wifi"]:
                adb("disconnect", d["serial"], timeout=5)
        # 4. stop the adb background service unless something else (e.g. a USB device) still uses it
        if not [d for d in list_devices(raw=True) if not d["wifi"]]:   # nothing on USB uses it
            adb("kill-server", timeout=5)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)   # glow theme is dark
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):   # logout / kill: clean up too
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.on_signal)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ASSETS)
        Gtk.Window.set_default_icon_name("phone-connect")

    def on_signal(self):
        win = self.props.active_window
        if win:
            win.close()
        else:
            self.quit()
        return GLib.SOURCE_REMOVE

        # Actions the Super App hub can start directly (over D-Bus, org.gtk.Actions)
        for name, fn in (("show-screen", lambda w: w.serial() and not w.running("mirror")
                          and w.toggle("mirror", w.mirror_cmd)),
                         ("send-files", lambda w: w.choose_files())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=fn: f(self.props.active_window or Win(self)))
            self.add_action(act)

    def do_activate(self):
        (self.props.active_window or Win(self)).present()


if __name__ == "__main__":
    App().run(None)

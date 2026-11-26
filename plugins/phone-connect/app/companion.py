"""Companion screen for Phone Connect: control panel, live monitor and drawing pad on a tablet or phone.

A tiny HTTP + WebSocket server listens on 127.0.0.1. The Android device reaches it through an
`adb reverse` tunnel (works over USB and wireless debugging).
Optional "Wi-Fi link": the same server over HTTPS on the local network (port 47801, opened in the firewall
for the local subnet only), for devices without wireless debugging (Android 10) - no cable after restarts.
Every API call needs a secret token (kept in ~/.config, 0600) that is only given to the device in its URL.
"""
import asyncio
import base64
import fcntl
import glob
import hashlib
import json
import os
import re
import secrets
import shlex
import socket
import ssl
import struct
import subprocess
import threading
import time

from gi.repository import Gio, GLib

PORT = 47800
LAN_PORT = 47801
LAN_DIR = os.path.expanduser("~/.local/share/phone-connect/lan")
TOKEN_FILE = os.path.expanduser("~/.config/phone-connect-token")


def lan_ip():
    """This laptop's address on the local network (the one used for the default route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))           # no packet is sent; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return None


def lan_subnet():
    ip = lan_ip()
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=5).stdout
        for m in re.finditer(r"inet (\S+)/(\d+)", out):
            if m.group(1) == ip:
                import ipaddress
                return str(ipaddress.ip_network(f"{ip}/{m.group(2)}", strict=False))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def persistent_token():
    try:
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
            if len(t) >= 20:
                return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


def lan_cert(ip):
    """Self-signed certificate for https://<ip>:47801 (re-made if the laptop's address changes)."""
    os.makedirs(LAN_DIR, exist_ok=True)
    crt, key, stamp = (os.path.join(LAN_DIR, n) for n in ("cert.pem", "key.pem", "ip.txt"))
    try:
        same = open(stamp).read().strip() == ip and os.path.exists(crt) and os.path.exists(key)
    except OSError:
        same = False
    if not same:
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                            "-subj", "/CN=Phone Connect", "-addext", f"subjectAltName=IP:{ip}",
                            "-keyout", key, "-out", crt], capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise OSError("could not create the Wi-Fi certificate: " + r.stderr[-200:])
        os.chmod(key, 0o600)
        with open(stamp, "w") as f:
            f.write(ip)
    return crt, key
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "assets", "companion.html")
PANEL_CFG = os.path.expanduser("~/.config/phone-connect-panel.json")
UDEV_RULE = "/etc/udev/rules.d/60-phone-connect-uinput.rules"
UINPUT_SETUP = (
    "set -e; "
    "echo 'KERNEL==\"uinput\", SUBSYSTEM==\"misc\", TAG+=\"uaccess\", OPTIONS+=\"static_node=uinput\"' "
    f"> {UDEV_RULE}; "
    "udevadm control --reload-rules; udevadm trigger --name-match=uinput --action=change || "
    "udevadm trigger -s misc --action=change")

DEFAULT_BUTTONS = [
    {"label": "Terminal", "icon": "terminal", "command": "ptyxis --new-window"},
    {"label": "VS Code", "icon": "code", "command": "/snap/bin/code"},
    {"label": "Files", "icon": "folder", "command": "nautilus --new-window"},
    {"label": "Chrome", "icon": "globe", "command": "google-chrome"},
    {"label": "Projects", "icon": "briefcase", "command": "nautilus --new-window ~/Documents"},
]


def run(cmd, timeout=4):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def panel_buttons():
    """Custom launcher buttons; the file is created with examples on first use."""
    try:
        with open(PANEL_CFG) as f:
            data = json.load(f)
        return [b for b in data.get("buttons", []) if b.get("label") and b.get("command")]
    except FileNotFoundError:
        try:
            os.makedirs(os.path.dirname(PANEL_CFG), exist_ok=True)
            with open(PANEL_CFG, "w") as f:
                json.dump({"_help": "Buttons shown on the tablet Control panel. command runs in your session; "
                                    "icon: terminal, code, folder, globe, briefcase, music, mail, chat, settings or app.", "buttons": DEFAULT_BUTTONS}, f, indent=2,
                          ensure_ascii=False)
        except OSError:
            pass
        return DEFAULT_BUTTONS
    except Exception:  # noqa: BLE001
        return DEFAULT_BUTTONS


# ---------------------------------------------------------------- laptop state
class Stats:
    """CPU / RAM / disk / battery / temperature / network / Docker, without extra packages."""

    def __init__(self):
        self.cpu_prev = None
        self.net_prev = None
        self.cache = {}
        self.cache_t = 0
        self.docker_t = 0
        self.docker = {"running": False, "containers": []}

    @staticmethod
    def _cpu_times():
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + parts[4]
        return sum(parts), idle

    @staticmethod
    def _net_bytes():
        rx = tx = 0
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, data = line.split(":", 1)
                name = name.strip()
                if name == "lo" or name.startswith(("docker", "br-", "veth", "virbr")):
                    continue
                v = data.split()
                rx += int(v[0])
                tx += int(v[8])
        return rx, tx, time.time()

    def docker_state(self):
        if time.time() - self.docker_t < 5:
            return self.docker
        self.docker_t = time.time()
        rc, out = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], timeout=3)
        if rc != 0:
            self.docker = {"running": False, "containers": []}
        else:
            rows = [line.split("\t") for line in out.splitlines() if line.count("\t") == 2]
            self.docker = {"running": True,
                           "containers": [{"name": n, "state": s, "status": st} for n, s, st in rows][:12]}
        return self.docker

    def snapshot(self):
        if time.time() - self.cache_t < 1.0 and self.cache:
            return self.cache
        total, idle = self._cpu_times()
        cpu = 0.0
        if self.cpu_prev:
            dt, di = total - self.cpu_prev[0], idle - self.cpu_prev[1]
            cpu = max(0.0, min(100.0, 100.0 * (1 - di / dt))) if dt > 0 else 0.0
        self.cpu_prev = (total, idle)

        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) * 1024
        ram_total, ram_avail = mem.get("MemTotal", 1), mem.get("MemAvailable", 0)

        st = os.statvfs("/")
        disk_total, disk_free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize

        batt = None
        for b in glob.glob("/sys/class/power_supply/BAT*"):
            try:
                cap = int(open(f"{b}/capacity").read())
                status = open(f"{b}/status").read().strip()
                batt = {"level": cap, "status": status}
                break
            except OSError:
                pass

        temp = None
        for z in glob.glob("/sys/class/thermal/thermal_zone*"):
            try:
                kind = open(f"{z}/type").read().strip()
                t = int(open(f"{z}/temp").read()) / 1000
                if kind == "x86_pkg_temp":
                    temp = t
                    break
                temp = max(temp or 0, t)
            except (OSError, ValueError):
                pass

        rx, tx, now = self._net_bytes()
        down = up = 0.0
        if self.net_prev:
            dt = max(0.1, now - self.net_prev[2])
            down, up = (rx - self.net_prev[0]) / dt, (tx - self.net_prev[1]) / dt
        self.net_prev = (rx, tx, now)

        rc, out = run(["ps", "-eo", "comm,%cpu,%mem", "--sort=-%cpu", "--no-headers"], timeout=3)
        top = []
        for line in out.splitlines()[:5]:
            m = re.match(r"(.+?)\s+([\d.]+)\s+([\d.]+)$", line.strip())
            if m:
                top.append({"name": m.group(1), "cpu": float(m.group(2)), "mem": float(m.group(3))})

        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])

        self.cache = {
            "cpu": round(cpu, 1), "cores": os.cpu_count(),
            "ram_used": ram_total - ram_avail, "ram_total": ram_total,
            "disk_used": disk_total - disk_free, "disk_total": disk_total,
            "battery": batt, "temp": temp, "net_down": down, "net_up": up,
            "top": top, "uptime": uptime, "docker": self.docker_state(),
        }
        self.cache_t = time.time()
        return self.cache


class Controls:
    """Things the Control panel can read and change on the laptop."""

    def __init__(self, app_actions):
        self.app_actions = app_actions      # {"phone_screen": callable, ...} from Phone Connect
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    # ---- audio (PipeWire)
    @staticmethod
    def _vol(target):
        rc, out = run(["wpctl", "get-volume", target])
        m = re.search(r"([\d.]+)", out)
        return {"level": round(float(m.group(1)) * 100) if m else None, "muted": "MUTED" in out}

    # ---- media (MPRIS)
    def _players(self):
        try:
            names = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "ListNames", None, GLib.VariantType("(as)"), 0, 2000, None).unpack()[0]
        except GLib.Error:
            return []
        out = []
        for n in names:
            if not n.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                props = self.bus.call_sync(n, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties",
                                           "GetAll", GLib.Variant("(s)", ("org.mpris.MediaPlayer2.Player",)),
                                           GLib.VariantType("(a{sv})"), 0, 2000, None).unpack()[0]
            except GLib.Error:
                continue
            meta = props.get("Metadata", {}) or {}
            artist = meta.get("xesam:artist") or []
            out.append({"bus": n, "status": props.get("PlaybackStatus", "Stopped"),
                        "title": meta.get("xesam:title") or n.split(".")[-1].capitalize(),
                        "artist": ", ".join(artist) if isinstance(artist, list) else str(artist)})
        out.sort(key=lambda p: p["status"] != "Playing")
        return out

    def _media_call(self, method):
        players = self._players()
        if players:
            try:
                self.bus.call_sync(players[0]["bus"], "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player",
                                   method, None, None, 0, 2000, None)
            except GLib.Error:
                pass

    # ---- brightness (GNOME 50: Mutter's backlight API on the built-in screen)
    def _brightness(self, step=None):
        try:
            serial, lights = self.bus.call_sync(
                "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.freedesktop.DBus.Properties",
                "Get", GLib.Variant("(ss)", ("org.gnome.Mutter.DisplayConfig", "Backlight")),
                None, 0, 2000, None).unpack()[0]
            light = next((b for b in lights if b.get("active")), None)
            if not light:
                return None
            lo, hi, val = light["min"], light["max"], light["value"]
            if step:
                val = max(lo, min(hi, val + (hi - lo) // 10 * (1 if step == "StepUp" else -1)))
                self.bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                                   "org.gnome.Mutter.DisplayConfig", "SetBacklight",
                                   GLib.Variant("(usi)", (serial, light["connector"], val)), None, 0, 2000, None)
            return round((val - lo) * 100 / max(1, hi - lo))
        except (GLib.Error, KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _gs(schema, key):
        return run(["gsettings", "get", schema, key])[1]

    def state(self):
        players = self._players()
        return {
            "volume": self._vol("@DEFAULT_AUDIO_SINK@"),
            "mic": self._vol("@DEFAULT_AUDIO_SOURCE@"),
            "media": players[0] if players else None,
            "brightness": self._brightness(),
            "dark": "dark" in self._gs("org.gnome.desktop.interface", "color-scheme"),
            "dnd": self._gs("org.gnome.desktop.notifications", "show-banners") == "false",
            "night": self._gs("org.gnome.settings-daemon.plugins.color", "night-light-enabled") == "true",
            "buttons": [{"label": b["label"], "icon": b.get("icon", "app")} for b in panel_buttons()],
            "phone": self.app_actions.get("phone_state", lambda: {})(),
        }

    def act(self, action, arg=None):
        sink, src = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
        simple = {
            "vol_up": ["wpctl", "set-volume", "-l", "1.0", sink, "5%+"],
            "vol_down": ["wpctl", "set-volume", sink, "5%-"],
            "mute": ["wpctl", "set-mute", sink, "toggle"],
            "mic": ["wpctl", "set-mute", src, "toggle"],
            "lock": ["loginctl", "lock-session"],
        }
        if action in simple:
            run(simple[action])
        elif action == "vol_set" and arg is not None:
            run(["wpctl", "set-volume", sink, f"{max(0, min(100, int(arg)))}%"])
        elif action in ("play", "next", "prev"):
            self._media_call({"play": "PlayPause", "next": "Next", "prev": "Previous"}[action])
        elif action in ("bright_up", "bright_down"):
            self._brightness("StepUp" if action == "bright_up" else "StepDown")
        elif action == "dark":
            dark = "dark" in self._gs("org.gnome.desktop.interface", "color-scheme")
            run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
                 "default" if dark else "prefer-dark"])
        elif action == "dnd":
            on = self._gs("org.gnome.desktop.notifications", "show-banners") == "false"
            run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true" if on else "false"])
        elif action == "night":
            on = self._gs("org.gnome.settings-daemon.plugins.color", "night-light-enabled") == "true"
            run(["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled",
                 "false" if on else "true"])
        elif action == "screenshot":   # opens GNOME's screenshot tool on the laptop
            subprocess.Popen(["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
                              "--object-path", "/org/freedesktop/portal/desktop",
                              "--method", "org.freedesktop.portal.Screenshot.Screenshot", "",
                              "{'interactive': <true>}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action == "launch" and arg is not None:
            buttons = panel_buttons()
            i = int(arg)
            if 0 <= i < len(buttons):
                subprocess.Popen(os.path.expanduser(buttons[i]["command"]), shell=True, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action in self.app_actions:
            self.app_actions[action]()
        time.sleep(0.15)
        return self.state()


# ---------------------------------------------------------------- virtual pen (uinput)
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x1a, 0x1b
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS = 0x140, 0x141, 0x14a, 0x14b
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045564, 0x40045565, 0x40045567, 0x4004556e
UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405c5503, 0x401c5504, 0x5501, 0x5502
INPUT_PROP_POINTER = 0x00
XY_MAX, P_MAX = 32767, 4095


def uinput_ready():
    return os.access("/dev/uinput", os.W_OK)


class VirtualPen:
    """A pen tablet device the desktop sees like a real drawing tablet (pressure + tilt + eraser)."""

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_SYN, EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for key in (BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_POINTER)
        axes = ((ABS_X, 0, XY_MAX, 100), (ABS_Y, 0, XY_MAX, 100), (ABS_PRESSURE, 0, P_MAX, 0),
                (ABS_TILT_X, -90, 90, 57), (ABS_TILT_Y, -90, 90, 57))
        for code, *_ in axes:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
        for code, lo, hi, res in axes:
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("H2x6i", code, 0, lo, hi, 0, 0, res))
        name = b"Phone Connect Pen"
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x06, 0x1d6b, 0x0104, 1, name, 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.near = self.down = False
        self.tool = BTN_TOOL_PEN

    def _ev(self, t, c, v):
        os.write(self.fd, struct.pack("llHHi", 0, 0, t, c, v))

    def event(self, kind, x, y, p, tx, ty, eraser, barrel):
        tool = BTN_TOOL_RUBBER if eraser else BTN_TOOL_PEN
        if self.near and tool != self.tool:           # switching pen <-> eraser: leave first
            self.leave()
        self.tool = tool
        if not self.near:
            self._ev(EV_KEY, tool, 1)
            self.near = True
        self._ev(EV_ABS, ABS_X, int(max(0.0, min(1.0, x)) * XY_MAX))
        self._ev(EV_ABS, ABS_Y, int(max(0.0, min(1.0, y)) * XY_MAX))
        self._ev(EV_ABS, ABS_TILT_X, int(tx))
        self._ev(EV_ABS, ABS_TILT_Y, int(ty))
        self._ev(EV_KEY, BTN_STYLUS, 1 if barrel else 0)
        if kind in ("d", "m") and (kind == "d" or self.down):
            if not self.down:
                self._ev(EV_KEY, BTN_TOUCH, 1)
                self.down = True
            self._ev(EV_ABS, ABS_PRESSURE, int(max(0.02, min(1.0, p)) * P_MAX))
        elif kind == "u" and self.down:
            self._ev(EV_ABS, ABS_PRESSURE, 0)
            self._ev(EV_KEY, BTN_TOUCH, 0)
            self.down = False
        self._ev(EV_SYN, 0, 0)
        if kind == "l":
            self.leave()

    def leave(self):
        if self.down:
            self._ev(EV_ABS, ABS_PRESSURE, 0)
            self._ev(EV_KEY, BTN_TOUCH, 0)
            self.down = False
        if self.near:
            self._ev(EV_KEY, self.tool, 0)
            self._ev(EV_SYN, 0, 0)
            self.near = False

    def close(self):
        try:
            self.leave()
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


# ---------------------------------------------------------------- server
class CompanionServer:
    def __init__(self, app_actions):
        self.token = persistent_token()     # stable, so a home-screen bookmark on the device keeps working
        self.lan_server = None
        self.lan_seen = 0                   # last request from a device over Wi-Fi
        self.lan_device = None              # its model, from the browser's user agent
        self.want = None                    # (time, view): ask open pages to switch view
        self.stats = Stats()
        self.controls = Controls(app_actions)
        self.pen = None
        self.loop = None
        self.thread = None
        self.server = None
        self.clients = 0
        self.lock = threading.Lock()
        # Camera / mic streamed from the device's browser: kind -> callable(bytes) set by the app
        self.sinks = {}
        self.stream_writers = {"cam": set(), "mic": set(), "spk": set()}
        self.cam_facing = "back"
        self.on_facing = None          # app callback when the page switches camera
        self.media_seen = 0

    # ---- lifecycle
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        ready = threading.Event()

        def main():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server = self.loop.run_until_complete(
                asyncio.start_server(self.handle, "127.0.0.1", PORT))
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=main, daemon=True)
        self.thread.start()
        ready.wait(5)

    def start_lan(self):
        """Also serve over HTTPS on the local network. Returns the URL for the device."""
        self.start()
        ip = lan_ip()
        if not ip:
            raise OSError("not connected to Wi-Fi")
        crt, key = lan_cert(ip)
        if self.lan_server:
            return self.lan_url(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        fut = asyncio.run_coroutine_threadsafe(
            asyncio.start_server(self.handle, "0.0.0.0", LAN_PORT, ssl=ctx), self.loop)
        self.lan_server = fut.result(5)
        return self.lan_url(ip)

    def stop_lan(self):
        if self.lan_server and self.loop:
            srv, self.lan_server = self.lan_server, None
            self.loop.call_soon_threadsafe(srv.close)

    def lan_url(self, ip=None):
        return f"https://{ip or lan_ip()}:{LAN_PORT}/?t={self.token}"

    def lan_alive(self):
        return time.time() - self.lan_seen < 7

    def ask_view(self, view):
        """Make open pages (e.g. the Wi-Fi bookmark on the tablet) switch to this view."""
        self.want = (time.time(), view)

    def stop(self):
        self.stop_lan()
        if self.loop:
            def shut():
                if self.server:
                    self.server.close()
                for task in asyncio.all_tasks(self.loop):   # open pen connections etc.
                    task.cancel()
                self.loop.call_later(0.2, self.loop.stop)
            self.loop.call_soon_threadsafe(shut)
        if self.pen:
            self.pen.close()
            self.pen = None

    def url(self, view):
        return f"http://localhost:{PORT}/?t={self.token}&view={view}"

    # ---- http
    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        except Exception:  # noqa: BLE001
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        path, _, query = target.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        authed = secrets.compare_digest(params.get("t", ""), self.token)
        sock = writer.get_extra_info("sockname")
        if authed and sock and sock[1] == LAN_PORT:
            self.lan_seen = time.time()
            m = re.search(r"Android [\d.]+; (?:[a-z]{2}-[a-z]{2}; )?([^;)]+?)(?: Build/[^;)]*)?\)",
                          headers.get("user-agent", ""))
            if m and m.group(1).strip() not in ("K", "wv"):
                self.lan_device = m.group(1).strip()

        if path == "/ws/pen" and authed and headers.get("upgrade", "").lower() == "websocket":
            await self.websocket(reader, writer, headers)
            return
        if path in ("/ws/cam", "/ws/mic", "/ws/spk") and authed and headers.get("upgrade", "").lower() == "websocket":
            await self.stream_socket(path[4:], reader, writer, headers)
            return
        body = b""
        if method == "POST":
            n = min(int(headers.get("content-length", "0") or 0), 65536)
            body = await reader.readexactly(n) if n else b""

        if path == "/" and method == "GET":
            try:
                with open(PAGE, "rb") as f:
                    await self.reply(writer, 200, f.read(), "text/html; charset=utf-8")
            except OSError:
                await self.reply(writer, 500, b"companion page missing", "text/plain")
        elif path == "/api/state" and authed:
            data = await asyncio.to_thread(self.full_state)
            await self.reply(writer, 200, json.dumps(data).encode(), "application/json")
        elif path == "/api/action" and authed and method == "POST":
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                req = {}
            data = await asyncio.to_thread(self.controls.act, str(req.get("id", "")), req.get("arg"))
            await self.reply(writer, 200, json.dumps({"controls": data}).encode(), "application/json")
        else:
            await self.reply(writer, 404 if authed or path == "/" else 403, b"not found", "text/plain")

    def full_state(self):
        want = self.want if self.want and time.time() - self.want[0] < 15 else None
        return {"stats": self.stats.snapshot(), "controls": self.controls.state(),
                "pen_ready": uinput_ready(), "time": time.time(),
                "want": {"id": want[0], "view": want[1]} if want else None}

    @staticmethod
    async def reply(writer, code, body, ctype):
        status = {200: "OK", 403: "Forbidden", 404: "Not Found", 500: "Error"}.get(code, "OK")
        writer.write(f"HTTP/1.1 {code} {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await writer.drain()
        finally:
            writer.close()

    # ---- websocket helpers
    @staticmethod
    async def ws_accept(writer, headers):
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        await writer.drain()

    @staticmethod
    async def ws_read(reader, limit=8 << 20):
        """One frame -> (opcode, payload). Fragmented frames are not used by browsers for these sizes."""
        b0, b1 = await reader.readexactly(2)
        op, n = b0 & 0x0F, b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await reader.readexactly(8))[0]
        if n > limit:
            raise ConnectionError("frame too large")
        mask = await reader.readexactly(4) if b1 & 0x80 else b"\0\0\0\0"
        raw = await reader.readexactly(n)
        if b1 & 0x80 and n:
            m = (mask * (n // 4 + 1))[:n]
            raw = (int.from_bytes(raw, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")
        return op, raw

    # ---- camera / microphone from the device's browser
    async def stream_socket(self, kind, reader, writer, headers):
        self.media_seen = time.time()
        await self.ws_accept(writer, headers)
        sink = self.sinks.get(kind)
        if not sink:
            await self.ws_send(writer, json.dumps({"error": "off"}))
            writer.close()
            return
        self.stream_writers[kind].add(writer)
        hello = {"ready": True}
        if kind == "cam":
            hello["facing"] = self.cam_facing
        await self.ws_send(writer, json.dumps(hello))
        try:
            while True:
                op, data = await self.ws_read(reader)
                if op == 8:
                    break
                if op == 9:
                    writer.write(bytes([0x8A, len(data)]) + data)
                    continue
                if op == 1:                      # settings from the page
                    try:
                        msg = json.loads(data.decode())
                    except ValueError:
                        continue
                    if kind == "cam" and msg.get("facing") in ("back", "front"):
                        self.cam_facing = msg["facing"]
                        if self.on_facing:
                            self.on_facing(self.cam_facing)
                    elif kind == "mic" and self.sinks.get("mic"):
                        self.sinks["mic"](msg)
                    continue
                if op == 2:
                    sink = self.sinks.get(kind)
                    if not sink:
                        break
                    try:
                        sink(data)
                    except (OSError, ValueError):
                        break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.stream_writers[kind].discard(writer)
            writer.close()

    def broadcast(self, kind, data):
        """Send binary data (laptop sound) to every page listening on this stream. Thread-safe."""
        if not self.loop or not self.stream_writers[kind]:
            return
        n = len(data)
        hdr = (bytes([0x82, n]) if n < 126 else bytes([0x82, 126]) + struct.pack(">H", n) if n < 65536
               else bytes([0x82, 127]) + struct.pack(">Q", n))
        frame = hdr + data

        def send():
            for w in list(self.stream_writers[kind]):
                try:
                    if w.transport.get_write_buffer_size() < 192000:   # drop audio rather than lag
                        w.write(frame)
                except Exception:  # noqa: BLE001
                    pass
        self.loop.call_soon_threadsafe(send)

    def media_connected(self, kind):
        return bool(self.stream_writers[kind])

    def end_stream(self, kind):
        """Stop a camera/mic stream: forget the sink and tell the page."""
        self.sinks.pop(kind, None)
        if not self.loop:
            return

        def close_all():
            for w in list(self.stream_writers[kind]):
                try:
                    msg = b'{"stop":true}'
                    w.write(bytes([0x81, len(msg)]) + msg + bytes([0x88, 0]))
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
        self.loop.call_soon_threadsafe(close_all)

    # ---- websocket (pen events)
    async def websocket(self, reader, writer, headers):
        await self.ws_accept(writer, headers)
        await self.ws_send(writer, json.dumps({"ready": uinput_ready()}))
        try:
            while True:
                op, data = await self.ws_read(reader, 1 << 20)
                if op == 8:
                    break
                if op == 9:
                    writer.write(bytes([0x8A, len(data)]) + data)
                    continue
                if op == 1:
                    err = self.pen_message(data.decode())
                    if err:
                        await self.ws_send(writer, json.dumps({"error": err}))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            with self.lock:
                if self.pen:
                    self.pen.leave()
            writer.close()

    @staticmethod
    async def ws_send(writer, text):
        data = text.encode()
        hdr = bytes([0x81, len(data)]) if len(data) < 126 else bytes([0x81, 126]) + struct.pack(">H", len(data))
        writer.write(hdr + data)
        await writer.drain()

    def pen_message(self, msg):
        """Lines like 'm 0.5123 0.2210 0.63 -12 8 0 0' = kind x y pressure tiltX tiltY eraser barrel."""
        with self.lock:
            if not self.pen:
                if not uinput_ready():
                    return "setup"
                try:
                    self.pen = VirtualPen()
                except OSError as e:
                    return f"pen: {e}"
            for line in msg.splitlines():
                v = line.split()
                if len(v) != 8:
                    continue
                try:
                    self.pen.event(v[0], float(v[1]), float(v[2]), float(v[3]), float(v[4]), float(v[5]),
                                   v[6] == "1", v[7] == "1")
                except (ValueError, OSError):
                    pass
        return None


def open_on_device(adb_path, serial, url):
    """Tunnel the port to the device and open the page full screen in its browser."""
    subprocess.run([adb_path, "-s", serial, "reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
                   capture_output=True, timeout=10)
    subprocess.run([adb_path, "-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                   capture_output=True, timeout=10)
    rc = subprocess.run([adb_path, "-s", serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                         "-d", shlex.quote(url)], capture_output=True, text=True, timeout=15)
    return rc.returncode == 0 and "Error" not in rc.stdout + rc.stderr


def close_on_device(adb_path, serial):
    subprocess.run([adb_path, "-s", serial, "reverse", "--remove", f"tcp:{PORT}"], capture_output=True, timeout=8)

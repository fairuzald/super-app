"""Everything Device Connect does differently on Linux, Windows and macOS, in one place.

Linux keeps the full feature set (avahi, PipeWire, v4l2loopback, GNOME). Windows and macOS get the core:
pairing, Wi-Fi connect, mirroring, screenshots, recording, files and the tablet pages.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import threading

from gi.repository import Gio, GLib

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not (IS_WIN or IS_MAC)
EXE = ".exe" if IS_WIN else ""

# What this OS can do. The UI hides tiles (and the tablet page hides buttons) for anything that is False.
FEATURES = {
    "webcam": IS_LINUX,          # v4l2loopback virtual camera
    "audio_bridge": IS_LINUX,    # PipeWire virtual mic / speaker
    "extra_display": IS_LINUX,   # GNOME Remote Desktop "extend" mode
    "pen": IS_LINUX,             # uinput virtual drawing tablet
    "notifications": IS_LINUX,   # GSConnect (GNOME extension)
    "firewall": IS_LINUX,        # ufw rule for the Wi-Fi link (Windows/macOS ask by themselves)
}

# Hide console windows of helper programs (adb, scrcpy) on Windows.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}


def home(*p):
    return os.path.join(os.path.expanduser("~"), *p)


def sdk_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or home("AppData", "Local"), "Android", "Sdk")
    if IS_MAC:
        return home("Library", "Android", "sdk")
    return home("Android", "Sdk")


def find_adb():
    """Prefer Google's SDK adb (distro builds drop Wi-Fi debugging connections), then PATH."""
    sdk = os.path.join(sdk_dir(), "platform-tools", "adb" + EXE)
    if os.access(sdk, os.X_OK):
        return sdk
    return shutil.which("adb") or sdk


def find_scrcpy():
    return shutil.which("scrcpy")


def pictures_dir():
    return home("Pictures", "Phone")


def videos_dir():
    return home("Movies" if IS_MAC else "Videos", "Phone")


def record_ext():
    # Windows can't stop scrcpy gracefully (no SIGINT to a GUI process); MKV stays playable when cut off.
    return ".mkv" if IS_WIN else ".mp4"


def stop_process(p):
    """Ask a helper (scrcpy, pw-cli, ffmpeg) to finish cleanly."""
    try:
        if IS_WIN:
            p.terminate()
        else:
            p.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        pass


def open_path(path):
    """Open a file or folder in the user's default app."""
    try:
        if IS_WIN:
            os.startfile(path)  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
    except (OSError, GLib.Error):
        pass


def write_qr_png(text, path, scale=8):
    """QR code as PNG: the pure-Python `segno` library if present (Windows/macOS builds ship it), else qrencode."""
    try:
        import segno
        segno.make(text, error="m").save(path, kind="png", scale=scale, border=2)
        return True
    except ImportError:
        pass
    try:
        subprocess.run(["qrencode", "-t", "PNG", "-s", str(scale), "-m", "2", "-o", path, text],
                       check=True, timeout=5, capture_output=True, **NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def scrcpy_install():
    """(how, command) to install scrcpy (+adb) on this OS, or (hint, None) when the user has to do it."""
    if IS_WIN:
        if shutil.which("winget"):
            return "winget", ["winget", "install", "--exact", "--id", "Genymobile.scrcpy",
                              "--accept-source-agreements", "--accept-package-agreements"]
        return "Install scrcpy from github.com/Genymobile/scrcpy (it includes adb).", None
    if IS_MAC:
        if shutil.which("brew"):
            return "brew", ["brew", "install", "scrcpy", "android-platform-tools"]
        return "Install Homebrew (brew.sh), then run: brew install scrcpy android-platform-tools", None
    pkgs = ["scrcpy"] + ([] if shutil.which("adb") or os.path.exists(find_adb()) else ["adb"])
    return "apt", ["pkexec", "apt-get", "install", "-y", *pkgs]


class AdbMdns:
    """mDNS discovery through adb's own resolver (`adb mdns services`), for systems without avahi.

    Same interface as the avahi browser: browse(type, on_new(name, ip, port), on_remove(name)) -> handle.
    """
    LINE = re.compile(r"^(\S+)\s+(_adb[\w.-]+)\s+(\d{1,3}(?:\.\d{1,3}){3}):(\d+)")

    def __init__(self, adb_path_fn, interval=3):
        self.adb_path_fn, self.interval = adb_path_fn, interval
        self.ok = True
        self.watch = {}            # handle id -> (type, on_new, on_remove, seen dict)
        self.next_id = 0
        self.busy = False
        GLib.timeout_add_seconds(self.interval, self._tick)

    def browse(self, typ, on_new, on_remove=None):
        self.next_id += 1
        self.watch[self.next_id] = (typ, on_new, on_remove, {})
        self._tick()
        return self.next_id

    def stop(self, handle):
        self.watch.pop(handle, None)

    def _tick(self):
        if self.watch and not self.busy:
            self.busy = True
            threading.Thread(target=self._scan, daemon=True).start()
        return True

    def _scan(self):
        found = {}
        try:
            out = subprocess.run([self.adb_path_fn(), "mdns", "services"], capture_output=True, text=True,
                                 timeout=8, **NO_WINDOW).stdout
            for line in out.splitlines():
                m = self.LINE.match(line.strip())
                if m:
                    found.setdefault(m.group(2).rstrip("."), {})[m.group(1)] = (m.group(3), int(m.group(4)))
        except (OSError, subprocess.SubprocessError):
            pass
        GLib.idle_add(self._apply, found)

    def _apply(self, found):
        self.busy = False
        for typ, on_new, on_remove, seen in list(self.watch.values()):
            now = found.get(typ, {})
            for name, (ip, port) in now.items():
                if seen.get(name) != (ip, port):
                    seen[name] = (ip, port)
                    on_new(name, ip, port)
            for name in [n for n in seen if n not in now]:
                seen.pop(name)
                if on_remove:
                    on_remove(name)
        return False

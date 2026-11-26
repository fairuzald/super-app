"""Laptop stats and controls for the tablet pages on Windows and macOS (Linux lives in companion.py).

Only the standard library: ctypes on Windows, built-in command-line tools on macOS.
Each Controls class lists what it supports in `caps`; the tablet page hides everything else.
"""
import os
import re
import shutil
import subprocess
import sys
import time

IS_WIN = sys.platform.startswith("win")
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}


def _run(cmd, timeout=4):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **NO_WINDOW)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def _docker(cache):
    if time.time() - cache.get("t", 0) < 5:
        return cache["v"]
    rc, out = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], timeout=3) \
        if shutil.which("docker") else (1, "")
    rows = [line.split("\t") for line in out.splitlines() if line.count("\t") == 2] if rc == 0 else []
    v = {"running": rc == 0, "containers": [{"name": n, "state": s, "status": st} for n, s, st in rows][:12]}
    cache.update(t=time.time(), v=v)
    return v


def _disk():
    du = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\" if IS_WIN else "/")
    return du.used, du.total


# ---------------------------------------------------------------- Windows
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class _MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _POWER(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class WindowsStats:
    def __init__(self):
        self.prev, self.cache, self.cache_t, self.dock = None, {}, 0, {}

    @staticmethod
    def _ft(f):
        return (f.hi << 32) | f.lo

    def snapshot(self):
        if time.time() - self.cache_t < 1.0 and self.cache:
            return self.cache
        k32 = ctypes.windll.kernel32
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        cpu = 0.0
        if k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            i, total = self._ft(idle), self._ft(kern) + self._ft(user)        # kernel time includes idle
            if self.prev and total > self.prev[1]:
                cpu = max(0.0, min(100.0, 100.0 * (1 - (i - self.prev[0]) / (total - self.prev[1]))))
            self.prev = (i, total)
        mem = _MEMSTAT()
        mem.dwLength = ctypes.sizeof(mem)
        k32.GlobalMemoryStatusEx(ctypes.byref(mem))
        pw, batt = _POWER(), None
        if k32.GetSystemPowerStatus(ctypes.byref(pw)) and pw.BatteryLifePercent <= 100 and pw.BatteryFlag != 128:
            batt = {"level": pw.BatteryLifePercent, "status": "Charging" if pw.ACLineStatus == 1 else "Discharging"}
        k32.GetTickCount64.restype = ctypes.c_ulonglong
        used, total_disk = _disk()
        self.cache = {"cpu": round(cpu, 1), "cores": os.cpu_count(), "ram_used": mem.ullTotalPhys - mem.ullAvailPhys,
                      "ram_total": mem.ullTotalPhys or 1, "disk_used": used, "disk_total": total_disk,
                      "battery": batt, "temp": None, "net_down": 0, "net_up": 0, "top": [],
                      "uptime": k32.GetTickCount64() / 1000, "docker": _docker(self.dock)}
        self.cache_t = time.time()
        return self.cache


class WindowsControls:
    """Media/volume keys like a keyboard's, plus lock, Snipping Tool and the dark-mode setting."""
    VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD, "play": 0xB3, "next": 0xB0, "prev": 0xB1}
    THEME = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    caps = {"vol_up", "vol_down", "mute", "play", "next", "prev", "lock", "screenshot", "dark", "launch"}

    def __init__(self, app_actions, buttons):
        self.app_actions, self.buttons = app_actions, buttons

    def _key(self, vk):
        u32 = ctypes.windll.user32
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, 2, 0)            # KEYEVENTF_KEYUP

    def _dark(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.THEME) as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False

    def _set_dark(self, on):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.THEME, 0, winreg.KEY_SET_VALUE) as k:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if on else 1)

    def state(self):
        return {"volume": None, "mic": None, "media": None, "brightness": None, "dark": self._dark(),
                "dnd": False, "night": False, "caps": sorted(self.caps | set(self.app_actions)),
                "buttons": [{"label": b["label"], "icon": b.get("icon", "app")} for b in self.buttons()],
                "phone": self.app_actions.get("phone_state", lambda: {})()}

    def act(self, action, arg=None):
        if action in self.VK:
            self._key(self.VK[action])
        elif action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "screenshot":
            os.startfile("ms-screenclip:")  # noqa: S606
        elif action == "dark":
            self._set_dark(not self._dark())
        elif action == "launch" and arg is not None:
            _launch(self.buttons(), arg)
        elif action in self.app_actions:
            self.app_actions[action]()
        time.sleep(0.15)
        return self.state()


# ---------------------------------------------------------------- macOS
def _osa(script):
    return _run(["osascript", "-e", script])


class MacStats:
    def __init__(self):
        self.cache, self.cache_t, self.dock = {}, 0, {}
        self.total = int(_run(["sysctl", "-n", "hw.memsize"])[1] or 0) or 1
        m = re.search(r"sec = (\d+)", _run(["sysctl", "-n", "kern.boottime"])[1])
        self.boot = int(m.group(1)) if m else time.time()

    def snapshot(self):
        if time.time() - self.cache_t < 2.0 and self.cache:
            return self.cache
        rc, out = _run(["ps", "-Ao", "%cpu=,%mem=,comm="])
        rows = []
        for line in out.splitlines():
            m = re.match(r"\s*([\d.]+)\s+([\d.]+)\s+(.+)$", line)
            if m:
                rows.append((float(m.group(1)), float(m.group(2)), os.path.basename(m.group(3))))
        cores = os.cpu_count() or 1
        cpu = min(100.0, sum(r[0] for r in rows) / cores)
        rows.sort(reverse=True)
        vm = _run(["vm_stat"])[1]
        page = int((re.search(r"page size of (\d+)", vm) or [0, 4096])[1])
        pages = {k: int(v) for k, v in re.findall(r"Pages (free|inactive|speculative):\s+(\d+)", vm)}
        avail = sum(pages.values()) * page
        b = re.search(r"(\d+)%;\s*(\w+)", _run(["pmset", "-g", "batt"])[1])
        used, total_disk = _disk()
        self.cache = {"cpu": round(cpu, 1), "cores": cores, "ram_used": max(0, self.total - avail),
                      "ram_total": self.total, "disk_used": used, "disk_total": total_disk,
                      "battery": {"level": int(b.group(1)), "status": b.group(2).capitalize()} if b else None,
                      "temp": None, "net_down": 0, "net_up": 0,
                      "top": [{"name": n, "cpu": c, "mem": m} for c, m, n in rows[:5]],
                      "uptime": time.time() - self.boot, "docker": _docker(self.dock)}
        self.cache_t = time.time()
        return self.cache


class MacControls:
    caps = {"vol_up", "vol_down", "vol_set", "mute", "lock", "screenshot", "dark", "launch"}

    def __init__(self, app_actions, buttons):
        self.app_actions, self.buttons = app_actions, buttons

    def _volume(self):
        rc, out = _osa("set s to get volume settings\nreturn (output volume of s as text) & \",\" & "
                       "(output muted of s as text)")
        m = re.match(r"(\d+),(true|false)", out)
        return {"level": int(m.group(1)), "muted": m.group(2) == "true"} if m else None

    def state(self):
        dark = _osa('tell application "System Events" to tell appearance preferences to return dark mode')[1]
        return {"volume": self._volume(), "mic": None, "media": None, "brightness": None, "dark": dark == "true",
                "dnd": False, "night": False, "caps": sorted(self.caps | set(self.app_actions)),
                "buttons": [{"label": b["label"], "icon": b.get("icon", "app")} for b in self.buttons()],
                "phone": self.app_actions.get("phone_state", lambda: {})()}

    def act(self, action, arg=None):
        if action in ("vol_up", "vol_down"):
            step = 6 if action == "vol_up" else -6
            _osa(f"set volume output volume ((output volume of (get volume settings)) + {step})")
        elif action == "vol_set" and arg is not None:
            _osa(f"set volume output volume {max(0, min(100, int(arg)))}")
        elif action == "mute":
            _osa("set volume output muted not (output muted of (get volume settings))")
        elif action == "lock":
            _run(["pmset", "displaysleepnow"])
        elif action == "screenshot":
            _run(["open", "-a", "Screenshot"])
        elif action == "dark":
            _osa('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
        elif action == "launch" and arg is not None:
            _launch(self.buttons(), arg)
        elif action in self.app_actions:
            self.app_actions[action]()
        time.sleep(0.15)
        return self.state()


def _launch(buttons, arg):
    """Run one of the user's own launcher buttons (from their panel config file)."""
    i = int(arg)
    if 0 <= i < len(buttons):
        subprocess.Popen(os.path.expanduser(buttons[i]["command"]), shell=True,  # noqa: S602 - user's own config
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)


DEFAULT_BUTTONS_WIN = [
    {"label": "Terminal", "icon": "terminal", "command": "start wt"},
    {"label": "VS Code", "icon": "code", "command": "code"},
    {"label": "Files", "icon": "folder", "command": "explorer"},
    {"label": "Browser", "icon": "globe", "command": "start https://"},
    {"label": "Projects", "icon": "briefcase", "command": "explorer %USERPROFILE%\\Documents"},
]
DEFAULT_BUTTONS_MAC = [
    {"label": "Terminal", "icon": "terminal", "command": "open -a Terminal"},
    {"label": "VS Code", "icon": "code", "command": "open -a 'Visual Studio Code'"},
    {"label": "Finder", "icon": "folder", "command": "open ~"},
    {"label": "Safari", "icon": "globe", "command": "open -a Safari"},
    {"label": "Projects", "icon": "briefcase", "command": "open ~/Documents"},
]

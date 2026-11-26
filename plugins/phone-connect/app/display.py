"""Extra display: use a tablet as a real extra monitor.

GNOME Remote Desktop can "extend" the desktop: each RDP client gets a new virtual
monitor of its own size, listed in Settings › Displays like a normal screen.
The tablet connects with Microsoft Remote Desktop to localhost:3389, which
`adb reverse` tunnels to this laptop, so no firewall port has to be opened.
RDP is switched on only while the extra display is in use.
"""
import json
import os
import secrets
import subprocess

PORT = 3389
USER = "tablet"
RDP_APP = "com.microsoft.rdc.androidx"
DIR = os.path.expanduser("~/.local/share/phone-connect/rdp")
CERT, KEY = os.path.join(DIR, "tls.crt"), os.path.join(DIR, "tls.key")
STATE = os.path.join(DIR, "state.json")
SCHEMA = "org.gnome.desktop.remote-desktop.rdp"
UNIT = "gnome-remote-desktop.service"


def run(cmd, timeout=15, **kw):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def gget(key):
    return run(["gsettings", "get", SCHEMA, key])[1]


def gset(key, value):
    run(["gsettings", "set", SCHEMA, key, value])


def available():
    return run(["which", "grdctl"])[0] == 0


def _load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(DIR, exist_ok=True)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)


def login():
    """(user, password) the tablet signs in with; created once and kept private (0600)."""
    d = _load()
    if not d.get("password"):
        d["password"] = secrets.token_urlsafe(9)
        d["configured"] = False
        _save(d)
    return USER, d["password"]


def setup():
    """One-time: TLS certificate + RDP login for GNOME Remote Desktop."""
    os.makedirs(DIR, exist_ok=True)
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        rc, out = run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                       "-subj", "/CN=Phone Connect", "-keyout", KEY, "-out", CERT], timeout=30)
        if rc:
            raise RuntimeError("Could not create the display certificate: " + out[-200:])
        os.chmod(KEY, 0o600)
    user, pw = login()
    d = _load()
    if not d.get("configured"):
        for cmd in (["grdctl", "rdp", "set-tls-cert", CERT], ["grdctl", "rdp", "set-tls-key", KEY],
                    ["grdctl", "rdp", "set-credentials", user, pw]):
            rc, out = run(cmd)
            if rc:
                raise RuntimeError(f"{' '.join(cmd[:3])} failed: {out[-200:]}")
        d["configured"] = True
        _save(d)


def is_on():
    return gget("enable") == "true" and run(["systemctl", "--user", "is-active", UNIT])[1] == "active"


def start(adb, serial):
    setup()
    d = _load()
    if "before" not in d:            # remember the user's own settings to put back later
        d["before"] = {k: gget(k) for k in ("screen-share-mode", "view-only", "negotiate-port", "port", "enable")}
        _save(d)
    gset("screen-share-mode", "'extend'")
    gset("view-only", "false")
    gset("negotiate-port", "false")
    gset("port", f"uint16 {PORT}")
    gset("enable", "true")
    rc, out = run(["systemctl", "--user", "restart", UNIT])
    if rc:
        raise RuntimeError("Could not start GNOME Remote Desktop: " + out[-200:])
    if not serial:                   # Wi-Fi link: the tablet connects straight to this laptop's address
        return True
    rc, out = run([adb, "-s", serial, "reverse", f"tcp:{PORT}", f"tcp:{PORT}"])
    if rc:
        raise RuntimeError("Could not reach the tablet: " + out[-200:])
    run([adb, "-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    rc, _ = run([adb, "-s", serial, "shell", "monkey", "-p", RDP_APP, "-c",
                 "android.intent.category.LAUNCHER", "1"])
    return rc == 0                   # False: Remote Desktop app not installed


def has_client(adb, serial):
    rc, out = run([adb, "-s", serial, "shell", "pm", "list", "packages", RDP_APP])
    return RDP_APP in out


def stop(adb=None, serial=None):
    """Turn RDP off again and restore the settings from before."""
    d = _load()
    before = d.pop("before", None) or {}
    run(["systemctl", "--user", "stop", UNIT])
    for k, v in before.items():
        if k != "enable" and v:
            gset(k, v)
    gset("enable", before.get("enable") or "false")
    _save(d)
    if adb and serial:
        run([adb, "-s", serial, "reverse", "--remove", f"tcp:{PORT}"], timeout=5)

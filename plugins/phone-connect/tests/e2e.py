#!/usr/bin/python3
"""End-to-end checks for Phone Connect against a real phone on Wi-Fi.

Run:  timeout -k 10 600 /usr/bin/python3 tests/e2e.py
The phone will lock/unlock and phone windows will open and close while it runs.
Your settings and the phone's screen timeout are put back at the end.
"""
import importlib.util, json, os, signal, subprocess, sys, threading, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pc", os.path.join(HERE, "..", "app", "phone-connect.py"))
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)
from gi.repository import GLib  # noqa: E402

pc.APP_ID = "local.fairuz.PhoneConnectE2E"
CFG_BACKUP = dict(pc.load_cfg())
results = []
app = pc.App()
win = None


# ---------------------------------------------------------------- helpers
def ui(fn, *a):
    """Run fn on the GTK thread and return its result."""
    box, ev = {}, threading.Event()

    def call():
        try:
            box["r"] = fn(*a)
        except Exception as e:      # surface UI errors in the test thread
            box["e"] = e
        ev.set()
        return False
    GLib.idle_add(call)
    ev.wait(20)
    if "e" in box:
        raise box["e"]
    return box.get("r")


def serial():
    return ui(win.serial)


def sh(cmd):
    return pc.adb("-s", serial(), "shell", cmd, timeout=10)


def wakefulness():
    out = sh("dumpsys power | grep -m1 mWakefulness=")
    return out.split("=", 1)[1].split()[0] if "=" in out else out


def timeout_ms():
    return sh("settings get system screen_off_timeout").strip()


def content_brightness():
    raw = subprocess.run([pc.ADB, "-s", serial(), "exec-out", "screencap"], capture_output=True,
                         timeout=20).stdout[16:]
    px = raw[::4 * 997]
    return sum(px) / max(1, len(px))


def windows():
    return int(subprocess.run(["pgrep", "-c", "-x", "scrcpy"], capture_output=True, text=True).stdout or 0)


def mirroring():
    return ui(win.running, "mirror")


def set_opt(k, v):
    ui(win.set_opt, k, v)


def wait_for(cond, secs, step=1.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def wake_phone():
    sh("input keyevent KEYCODE_WAKEUP")
    wait_for(lambda: wakefulness() == "Awake", 6)


def lock_phone():
    sh("input keyevent KEYCODE_POWER") if wakefulness() == "Awake" else None


def start_mirror():
    if not mirroring():
        ui(win.toggle, "mirror", win.mirror_cmd)
    return wait_for(mirroring, 5)


def stop_all():
    for n in list(ui(lambda: list(win.tasks))):
        ui(win.stop_task, n)
    wait_for(lambda: windows() == 0, 10)


def case(name):
    def deco(fn):
        def run():
            t = time.time()
            try:
                ok, detail = fn()
            except Exception as e:
                ok, detail = False, f"error: {e!r}\n{traceback.format_exc(limit=2)}"
            results.append((name, ok, detail, time.time() - t))
            print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t:.0f}s)  {detail}", flush=True)
            stop_all()
        CASES.append(run)
        return fn
    return deco


CASES = []
ORIG_TIMEOUT = None


# ---------------------------------------------------------------- cases
@case("1. App connects to the phone by itself")
def c1():
    ok = wait_for(lambda: serial() is not None, 40, 2)
    return ok, f"serial={serial()}"


@case("2. Dark mode: phone screen off, window stays live past the phone timeout")
def c2():
    set_opt("phone_mode", 0)
    wake_phone()
    start_mirror()
    time.sleep(22)                                    # phone timeout is 15 s
    args = " ".join(ui(lambda: win.tasks["mirror"].args))
    live, state, tmo = content_brightness(), wakefulness(), timeout_ms()
    ok = mirroring() and "--turn-screen-off" in args and state == "Awake" and live > 5
    return ok, f"window open={mirroring()}, phone={state}, picture brightness={live:.0f}, timeout during={tmo}"


@case("3. Lock phone with power button → window closes, phone stays locked, timeout restored")
def c3():
    set_opt("phone_mode", 0)
    set_opt("on_lock", 0)
    wake_phone()
    start_mirror()
    time.sleep(8)
    lock_phone()
    closed = wait_for(lambda: not mirroring() and windows() == 0, 20)
    time.sleep(8)
    state, tmo = wakefulness(), timeout_ms()
    ok = closed and state != "Awake" and tmo == ORIG_TIMEOUT
    return ok, f"window closed={closed}, phone after={state}, timeout={tmo} (orig {ORIG_TIMEOUT})"


@case("4. Unlock phone afterwards → no window pops up by itself")
def c4():
    wake_phone()
    time.sleep(12)
    n = windows()
    lock_phone()
    return n == 0 and not mirroring(), f"phone windows open={n}"


@case("5. Hide phone screen also closes app windows")
def c5():
    wake_phone()
    start_mirror()
    time.sleep(3)
    ui(win.open_app, "Calendar", "com.transsion.calendar")
    time.sleep(6)
    before = windows()
    ui(win.toggle, "mirror", win.mirror_cmd)
    closed = wait_for(lambda: windows() == 0, 10)
    return before >= 2 and closed, f"windows before={before}, all closed={closed}"


@case("6. Close the phone window with X → app updates, timeout restored")
def c6():
    wake_phone()
    start_mirror()
    time.sleep(6)
    p = ui(lambda: win.tasks["mirror"])
    p.send_signal(signal.SIGTERM)                      # same as closing the window
    gone = wait_for(lambda: not mirroring(), 10)
    label = ui(lambda: win.mirror_content.get_label())
    time.sleep(2)
    tmo = timeout_ms()
    return gone and label == "Show phone screen" and tmo == ORIG_TIMEOUT, \
        f"app shows '{label}', timeout={tmo}"


@case("7. Change a setting while mirroring → applied right away")
def c7():
    set_opt("phone_mode", 0)
    wake_phone()
    start_mirror()
    time.sleep(6)
    set_opt("phone_mode", 1)                           # On
    ok = wait_for(lambda: mirroring() and "--turn-screen-off" not in
                  " ".join(ui(lambda: win.tasks["mirror"].args)), 15)
    set_opt("phone_mode", 0)
    wait_for(lambda: mirroring() and "--turn-screen-off" in " ".join(ui(lambda: win.tasks["mirror"].args)), 15)
    return ok, f"restarted with new setting={ok}"


@case("8. 'Phone timeout' mode: phone sleeps → window closes by itself")
def c8():
    set_opt("phone_mode", 2)
    set_opt("on_lock", 0)
    wait_for(lambda: timeout_ms() == ORIG_TIMEOUT, 20)   # previous session's timeout must be back
    wake_phone()
    start_mirror()
    closed = wait_for(lambda: not mirroring(), 40, 2)
    set_opt("phone_mode", 0)
    return closed, f"window closed after phone slept={closed}"


@case("9. 'Keep window open' on lock → banner shows, Wake button wakes the phone")
def c9():
    set_opt("phone_mode", 0)
    set_opt("on_lock", 1)
    wake_phone()
    start_mirror()
    time.sleep(8)
    lock_phone()
    banner = wait_for(lambda: ui(win.sleep_banner.get_revealed), 15)
    still_open = mirroring()
    ui(win.wake_unlock)
    awake = wait_for(lambda: wakefulness() == "Awake", 10)
    set_opt("on_lock", 0)
    return banner and still_open and awake, f"banner={banner}, window kept={still_open}, woke={awake}"


@case("10. Wi-Fi connection drops → window ends, app reconnects, no window pops up")
def c10():
    wake_phone()
    start_mirror()
    time.sleep(5)
    pc.adb("disconnect", timeout=5)
    ended = wait_for(lambda: not mirroring(), 15)
    back = wait_for(lambda: serial() is not None, 40, 2)
    time.sleep(5)
    return ended and back and windows() == 0, f"window ended={ended}, reconnected={back}, windows={windows()}"


@case("11. Phone window killed abruptly → phone timeout still restored")
def c11():
    set_opt("phone_mode", 0)
    wake_phone()
    start_mirror()
    time.sleep(6)
    during = timeout_ms()
    ui(lambda: win.tasks["mirror"].kill())
    wait_for(lambda: not mirroring(), 10)
    restored = wait_for(lambda: timeout_ms() == ORIG_TIMEOUT, 15)
    return restored, f"during={during}, after={timeout_ms()}"


# ---------------------------------------------------------------- runner
def runner():
    global ORIG_TIMEOUT
    try:
        wait_for(lambda: serial() is not None, 40, 2)
        ORIG_TIMEOUT = timeout_ms()
        print(f"phone {serial()}, its screen timeout {ORIG_TIMEOUT} ms\n", flush=True)
        for run in CASES:
            run()
        # 12. closing the app turns everything off
        t = time.time()
        wake_phone()
        start_mirror()
        time.sleep(5)
        ui(win.close)
        time.sleep(12)
        adb_up = subprocess.run(["pgrep", "-x", "adb"], capture_output=True).returncode == 0
        ok = windows() == 0 and not adb_up
        results.append(("12. Close app → all windows closed, phone disconnected, adb stopped", ok,
                        f"windows={windows()}, adb running={adb_up}", time.time() - t))
        print(f"{'PASS' if ok else 'FAIL'}  12. Close app → everything off  windows={windows()} adb={adb_up}",
              flush=True)
    finally:
        pc.save_cfg(CFG_BACKUP)
        if ORIG_TIMEOUT:
            subprocess.run([pc.ADB, "start-server"], capture_output=True)
            for d in pc.list_devices():
                pc.adb("-s", d["serial"], "shell", "settings", "put", "system", "screen_off_timeout",
                       ORIG_TIMEOUT, timeout=8)
        subprocess.run(["pkill", "-x", "scrcpy"])
        passed = sum(1 for r in results if r[1])
        print(f"\n{passed}/{len(results)} passed", flush=True)
        with open(os.path.join(HERE, "last-results.json"), "w") as f:
            json.dump([{"case": n, "pass": ok, "detail": d, "seconds": round(s)} for n, ok, d, s in results], f,
                      indent=1)
        os._exit(0 if passed == len(results) else 1)


def go():
    global win
    win = app.props.active_window
    threading.Thread(target=runner, daemon=True).start()
    return False


app.connect("activate", lambda *_: GLib.timeout_add(1500, go))
app.hold()          # keep running after the window closes (case 12)
app.run(None)
while True:         # the runner thread ends the process
    time.sleep(1)

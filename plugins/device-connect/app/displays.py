#!/usr/bin/python3
"""Display keeper: runs in the background (systemd user service) and, whenever screens change,

  * keeps the laptop screen at 100 % scale,
  * puts every screen on its highest refresh rate (at its current resolution),
  * keeps the laptop as the primary display (unless you picked another one yourself),
  * remembers how you arranged each combination of screens and puts it back next time
    (so the tablet extra display lands where you left it).

Uses Mutter's DisplayConfig D-Bus API, the same one GNOME Settings uses.
"""
import json
import os
import sys

from gi.repository import Gio, GLib

BUS, PATH, IFACE = "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig"
SAVE = os.path.expanduser("~/.config/phone-connect-displays.json")
LOG = "--verbose" in sys.argv


def log(*a):
    if LOG:
        print(*a, flush=True)


def load():
    try:
        with open(SAVE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(d):
    tmp = SAVE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, SAVE)


class Keeper:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.saved = load()
        self.last_set = None
        self.pending = 0
        self.bus.signal_subscribe(BUS, IFACE, "MonitorsChanged", PATH, None, 0,
                                  lambda *a: self.schedule())
        self.schedule(500)

    def call(self, method, args=None):
        return self.bus.call_sync(BUS, PATH, IFACE, method, args, None, 0, 5000, None).unpack()

    def schedule(self, ms=1200):
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(ms, self.check)

    # ------------------------------------------------------------ main step
    def check(self):
        self.pending = 0
        try:
            self.fix()
        except GLib.Error as e:
            log("error:", e.message)
        return False

    def fix(self):
        serial, monitors, logical, props = self.call("GetCurrentState")
        mons = {}   # connector -> info
        for (conn, vendor, product, ser), modes, mprops in monitors:
            cur = next((m for m in modes if m[6].get("is-current")), None)
            if not cur:
                continue                    # disabled screen: leave it alone
            builtin = bool(mprops.get("is-builtin"))
            same_res = [m for m in modes if (m[1], m[2]) == (cur[1], cur[2])]
            best = max(same_res, key=lambda m: m[3])
            mons[conn] = {"key": f"{vendor}|{product}|{ser}", "builtin": builtin, "virtual": conn.startswith("Meta-"), "mode": best[0],
                          "w": best[1], "h": best[2], "scales": best[5], "cur_mode": cur[0]}
        if not mons:
            return
        layout_logical = props.get("layout-mode", 1) == 1
        set_key = "+".join(sorted(m["key"] for m in mons.values()))

        cur_lms = []
        for x, y, scale, transform, primary, conns, _p in logical:
            cs = [c[0] for c in conns if c[0] in mons]
            if cs:
                cur_lms.append({"x": x, "y": y, "scale": scale, "transform": transform,
                                "primary": primary, "conns": cs})
        if not cur_lms:
            return
        # Mutter may report a remote (virtual) screen as primary too; keep exactly one primary,
        # preferring a real screen.
        prims = [lm for lm in cur_lms if lm["primary"]]
        if len(prims) > 1:
            real = [lm for lm in prims if not any(mons[c]["virtual"] for c in lm["conns"])]
            keep = (real or prims)[0]
            for lm in cur_lms:
                lm["primary"] = lm is keep

        # Same screens as last time and only the arrangement differs -> the user arranged them: remember it.
        if self.last_set == set_key:
            self.remember(set_key, mons, cur_lms)
        new_set = self.last_set != set_key
        self.last_set = set_key

        want = [dict(lm) for lm in cur_lms]
        for lm in want:
            if any(mons[c]["builtin"] for c in lm["conns"]):
                if any(abs(s - 1.0) < 1e-3 for s in mons[lm["conns"][0]]["scales"]):
                    lm["scale"] = 1.0

        saved = self.saved.get(set_key)
        if new_set and saved:
            for lm in want:                       # put back the remembered arrangement
                s = saved.get(mons[lm["conns"][0]]["key"])
                if s:
                    lm["x"], lm["y"], lm["primary"] = s["x"], s["y"], s["primary"]
        elif new_set and not saved:
            # a new combination: keep the laptop as primary
            if any(any(mons[c]["builtin"] for c in lm["conns"]) for lm in want):
                for lm in want:
                    lm["primary"] = any(mons[c]["builtin"] for c in lm["conns"])
            self.remember(set_key, mons, want)

        def size(lm):
            m = mons[lm["conns"][0]]
            w, h = (m["h"], m["w"]) if lm["transform"] % 2 else (m["w"], m["h"])
            return (w / lm["scale"], h / lm["scale"]) if layout_logical else (w, h)

        modes_change = any(m["mode"] != m["cur_mode"] for m in mons.values())
        same = (not modes_change and
                all((a["x"], a["y"], round(a["scale"], 3), a["primary"]) == (b["x"], b["y"], round(b["scale"], 3), b["primary"])
                    for a, b in zip(want, cur_lms)))
        if same:
            return

        def build(lms):
            return [(int(lm["x"]), int(lm["y"]), float(lm["scale"]), int(lm["transform"]), bool(lm["primary"]),
                     [(c, mons[c]["mode"], {}) for c in lm["conns"]]) for lm in lms]

        def apply(lms, method):
            args = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, method, build(lms), {}))
            self.call("ApplyMonitorsConfig", args)

        for attempt in (want, reflow(want, size)):
            try:
                apply(attempt, 0)                 # verify first
                apply(attempt, 2)                 # then apply and keep (like GNOME Settings)
                log("applied", set_key, [(lm["conns"], lm["x"], lm["y"], lm["scale"], lm["primary"]) for lm in attempt])
                return
            except GLib.Error as e:
                log("rejected:", e.message)

    def remember(self, set_key, mons, lms):
        entry = {mons[lm["conns"][0]]["key"]: {"x": lm["x"], "y": lm["y"], "primary": lm["primary"]} for lm in lms}
        if self.saved.get(set_key) != entry:
            self.saved[set_key] = entry
            save(self.saved)
            log("remembered", set_key, entry)


def reflow(lms, size):
    """Make the layout valid again after sizes changed: keep each screen on the same side of the primary."""
    lms = [dict(lm) for lm in lms]
    prim = next((lm for lm in lms if lm["primary"]), lms[0])
    pw, ph = size(prim)
    pcx, pcy = prim["x"] + pw / 2, prim["y"] + ph / 2
    sides = {"right": [], "left": [], "below": [], "above": []}
    for lm in lms:
        if lm is prim:
            continue
        w, h = size(lm)
        dx, dy = lm["x"] + w / 2 - pcx, lm["y"] + h / 2 - pcy
        side = ("right" if dx > 0 else "left") if abs(dx) / pw >= abs(dy) / ph else ("below" if dy > 0 else "above")
        sides[side].append(lm)
    prim["x"], prim["y"] = 0, 0
    x = pw
    for lm in sorted(sides["right"], key=lambda l: l["x"]):
        w, h = size(lm); lm["x"], lm["y"] = x, 0; x += w
    x = 0
    for lm in sorted(sides["left"], key=lambda l: -l["x"]):
        w, h = size(lm); x -= w; lm["x"], lm["y"] = x, 0
    y = ph
    for lm in sorted(sides["below"], key=lambda l: l["y"]):
        w, h = size(lm); lm["x"], lm["y"] = 0, y; y += h
    y = 0
    for lm in sorted(sides["above"], key=lambda l: -l["y"]):
        w, h = size(lm); y -= h; lm["x"], lm["y"] = 0, y
    mx, my = min(lm["x"] for lm in lms), min(lm["y"] for lm in lms)
    for lm in lms:
        lm["x"] -= mx; lm["y"] -= my
    return lms


if __name__ == "__main__":
    Keeper()
    loop = GLib.MainLoop()
    if "--once" in sys.argv:
        GLib.timeout_add(4000, loop.quit)
    loop.run()

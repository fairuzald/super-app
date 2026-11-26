#!/usr/bin/python3
"""End-to-end checks for Storage Cleaner on a fake home folder with dummy data.

Run:  timeout -k 10 300 /usr/bin/python3 tests/e2e.py
A Storage Cleaner window opens for about a minute. Nothing in your real home is
touched: the app is pointed at ~/sc-e2e-sandbox (removed at the end) and uses
its own Trash inside it. Screenshots land in tests/shots/.
"""
import json, os, shutil, sys, threading, time, traceback

REAL_HOME = os.path.expanduser("~")
SANDBOX = os.path.join(REAL_HOME, "sc-e2e-sandbox")        # same disk as home, so Trash works like real use
os.environ["SC_HOME"] = SANDBOX
os.environ["XDG_DATA_HOME"] = os.path.join(SANDBOX, ".local", "share")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")
REAL_TRASH = os.path.join(REAL_HOME, ".local", "share", "Trash", "files")
MB = 1 << 20
DAY = 86400


def put(path, size=2 * MB, age_days=0, data=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if data is not None else os.urandom(size))
    age(path, age_days)


def age(path, days):
    if days:
        t = time.time() - days * DAY
        os.utime(path, (t, t), follow_symlinks=False)


def make_sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    S = lambda *p: os.path.join(SANDBOX, *p)
    for d in ("Documents", "Downloads", ".config", ".local/share/Trash/files", ".local/share/Trash/info"):
        os.makedirs(S(d), exist_ok=True)
    # caches
    put(S(".cache/pip/http/a.bin"))
    put(S(".cache/yarn/v6/pkg.tgz"), 3 * MB)
    put(S(".cache/thumbnails/large/x.png"))
    put(S(".cache/mystery-app/blob"))
    put(S(".npm/_cacache/content/x"), 2 * MB)
    # projects
    put(S("Documents/projA/node_modules/left-pad/index.js"), 3 * MB)
    put(S("Documents/projA/package.json"), data=b'{"name":"projA"}')
    put(S("Documents/projA/index.js"), data=b"//")
    put(S("Documents/projB/node_modules/react/index.js"), 2 * MB)
    put(S("Documents/projB/package.json"), data=b'{"name":"projB"}')
    put(S("Documents/projC/.venv/pyvenv.cfg"), data=b"home = /usr/bin")
    put(S("Documents/projC/.venv/lib/site.py"), 2 * MB)
    put(S("Documents/projC/app/__pycache__/m.cpython-314.pyc"), 2 * MB)
    put(S("Documents/projC/pyproject.toml"), data=b"[project]")
    for p in ("Documents/projA/node_modules", "Documents/projA/package.json", "Documents/projA/index.js",
              "Documents/projA", "Documents/projC/.venv", "Documents/projC/pyproject.toml",
              "Documents/projC/app", "Documents/projC"):
        age(S(p), 120)
    # downloads
    put(S("Downloads/old-tool.deb"), 2 * MB, 100)
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1400, 1000)
    pb.fill(0x3584e4ff)
    pb.savev(S("Downloads/holiday.png"), "png", [], [])
    with open(S("Downloads/holiday.png"), "ab") as f:
        f.write(os.urandom(2 * MB))           # make it big enough to show
    age(S("Downloads/holiday.png"), 200)
    blob = os.urandom(2 * MB)
    put(S("Downloads/photos.zip"), data=blob, age_days=5)
    put(S("Downloads/photos (1).zip"), data=blob, age_days=1)
    put(S("Downloads/movie.mp4.crdownload"), 2 * MB, 2)
    put(S("Downloads/report.pdf"), 2 * MB, 3)
    put(S("Downloads/notes.zip"), 2 * MB, 1)
    put(S("Downloads/notes/readme.txt"), data=b"hi")
    put(S("Downloads/old-project/data.bin"), 2 * MB, 150)
    age(S("Downloads/old-project"), 150)
    # big folder for the explorer + a large file
    put(S("Videos/big-video.mkv"), 6 * MB, 30)
    put(S("bigfolder/part1"), 4 * MB)
    # something already in the (sandbox) Trash
    put(S("Documents/old-draft.txt"), 2 * MB)


make_sandbox()

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("sc", os.path.join(HERE, "..", "app", "storage-cleaner.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)
core = sc.core
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

assert core.HOME == os.path.realpath(SANDBOX), core.HOME
assert core.TRASH.startswith(core.HOME), core.TRASH
Gio.File.new_for_path(os.path.join(SANDBOX, "Documents", "old-draft.txt")).trash(None)

sc.APP_ID = "local.fairuz.StorageCleanerE2E"
app = sc.App()
win = None
results = []
real_trash_before = sorted(os.listdir(REAL_TRASH)) if os.path.isdir(REAL_TRASH) else []
gaps = {"max": 0, "last": None, "on": True}


def ui(fn, *a):
    box, ev = {}, threading.Event()

    def call():
        try:
            box["r"] = fn(*a)
        except Exception as e:
            box["e"] = e
        ev.set()
        return False
    GLib.idle_add(call)
    ev.wait(30)
    if "e" in box:
        raise box["e"]
    return box.get("r")


def wait(cond, timeout=60, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if ui(cond):
            return True
        time.sleep(step)
    return False


PAINT = {}


def shot(name):
    """Save a PNG of the window (the paintable needs a frame before it has content)."""
    box = {}
    time.sleep(0.4)
    if "p" not in PAINT:        # one paintable for the whole run: it follows every redraw of the window
        PAINT["p"] = ui(Gtk.WidgetPaintable.new, win)
        time.sleep(0.5)
    box["p"] = PAINT["p"]
    ui(win.queue_draw)
    for _ in range(10):
        time.sleep(0.2)

        def do():
            p = box["p"]
            snap = Gtk.Snapshot()
            p.snapshot(snap, win.get_width(), win.get_height())
            node = snap.to_node()
            if not node:
                return False
            win.get_renderer().render_texture(node, None).save_to_png(os.path.join(SHOTS, name + ".png"))
            return True
        if ui(do):
            return


def case(name):
    def deco(fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception:
            ok, detail = False, traceback.format_exc(limit=4)
        results.append({"case": name, "pass": bool(ok), "detail": detail, "seconds": round(time.time() - t0)})
        print(("PASS " if ok else "FAIL ") + name + "  —  " + str(detail).splitlines()[-1][:200], flush=True)
        return fn
    return deco


def S(*p):
    return os.path.join(SANDBOX, *p)


def page():
    return win.nav.get_visible_page()


def items_of(cid):
    return {i.title: i for i in win.cats[cid].items}


def dialog():
    return win.get_visible_dialog()


def answer(resp):
    d = dialog()
    assert d is not None, "no dialog shown"
    d.emit("response", resp)
    d.force_close()


def open_cat(cid):
    ui(lambda: win.nav.pop_to_tag("home"))
    ui(win.open_category, cid)
    wait(lambda: not page().scanning())


def tick():
    now = time.monotonic()
    if gaps["last"] is not None:
        gaps["max"] = max(gaps["max"], now - gaps["last"])
    gaps["last"] = now
    return gaps["on"]


def tests():
    global win
    time.sleep(1)
    win = app.props.active_window
    ui(lambda: GLib.timeout_add(50, tick))

    @case("1. Opens and scans everything in the background without freezing")
    def _():
        done = wait(lambda: not win.pending, 120)
        gaps["on"] = False
        shot("01-home")
        return done and gaps["max"] < 0.5, f"scan finished={done}, longest UI pause={gaps['max'] * 1000:.0f} ms"

    @case("2. Real sizes: disk bar and home folder size are filled in")
    def _():
        t = ui(win.disk_title.get_label)
        sub = ui(win.disk_sub.get_label)
        return " of " in t and "home folder uses" in sub, f"{t} | {sub}"

    @case("3. App caches: safe ones ticked, unknown ones not")
    def _():
        open_cat("caches")
        it = items_of("caches")
        shot("03-caches")
        ok = it["pip downloads (Python)"].checked and it["Yarn package downloads"].checked \
            and it["Picture thumbnails"].checked and not it["mystery-app"].checked
        return ok, {k: v.checked for k, v in it.items()}

    @case("4. Cache cleanup → preview → Move to Trash → delete just those from Trash")
    def _():
        ui(page().confirm)
        time.sleep(0.4)
        d = ui(dialog)
        has = ui(lambda: (d.has_response("trash"), d.has_response("delete")))
        body = ui(d.get_body)
        shot("04-confirm")
        ui(answer, "trash")
        wait(lambda: dialog() is not None and not page().busy, 30)
        res_head = ui(lambda: dialog().get_heading())
        shot("05-result")
        gone = not any(os.path.exists(S(".cache", n)) for n in ("pip", "yarn", "thumbnails"))
        in_trash = {os.path.basename(e[1]) for e in core.trash_entries()} >= {"pip", "yarn", "thumbnails"}
        kept = os.path.exists(S(".cache", "mystery-app"))
        ui(answer, "empty")
        wait(lambda: not any(os.path.basename(e[1]) == "yarn" for e in core.trash_entries()), 20)
        left = {os.path.basename(e[1]) for e in core.trash_entries()}
        emptied = not ({"pip", "yarn", "thumbnails"} & left) and "old-draft.txt" in left
        ok = has == (True, True) and "pip" in body and gone and in_trash and kept and emptied and "Trash" in res_head
        return ok, (f"buttons trash/delete={has}, removed={gone}, went to Trash={in_trash}, untouched kept={kept}, "
                    f"'Delete for good' removed only those={emptied}, result='{res_head}'")

    @case("5. node_modules: old project ticked, recent one not; Delete now removes only that folder")
    def _():
        open_cat("node")
        it = items_of("node")
        shot("06-node")
        a, b = it.get("projA"), it.get("projB")
        if not (a and b and a.checked and not b.checked):
            return False, {k: (v.checked, v.subtitle) for k, v in it.items()}
        ui(page().confirm)
        time.sleep(0.4)
        ui(answer, "delete")
        wait(lambda: dialog() is not None and not page().busy, 30)
        head = ui(lambda: dialog().get_heading())
        ui(answer, "ok")
        ok = (not os.path.exists(S("Documents/projA/node_modules")) and os.path.exists(S("Documents/projA/package.json"))
              and os.path.exists(S("Documents/projB/node_modules")))
        return ok, f"projA node_modules gone, package.json kept, projB kept={ok}; result='{head}'"

    @case("6. Python & build: __pycache__ always ticked, old .venv ticked")
    def _():
        open_cat("build")
        it = items_of("build")
        shot("07-build")
        pyc = [v for k, v in it.items() if "bytecode" in k]
        venv = [v for k, v in it.items() if "Virtual environment" in k]
        return bool(pyc and pyc[0].checked and venv and venv[0].checked), list(it)

    @case("7. Downloads: duplicates, unfinished, old installer, unzipped archive and old photo found")
    def _():
        open_cat("downloads")
        it = items_of("downloads")
        time.sleep(1.5)                     # let thumbnails load
        shot("08-downloads")
        want = {"photos (1).zip": ("Duplicates", True), "movie.mp4.crdownload": ("Unfinished downloads", True),
                "old-tool.deb": ("Installers", True), "notes.zip": ("Archives", True),
                "holiday.png": ("Images", False), "old-project": ("Folders", False)}
        got = {k: (it[k].group, it[k].checked) if k in it else None for k in want}
        ok = got == want and "report.pdf" not in it and "photos.zip" not in it
        return ok, got

    @case("8. Downloads clean: own files only offer Move to Trash")
    def _():
        def only_dup():
            for _c, i in page().entries:
                i.checked = i.title == "photos (1).zip"
            page().reload()
        ui(only_dup)
        ui(page().confirm)
        time.sleep(0.4)
        d = ui(dialog)
        has = ui(lambda: (d.has_response("trash"), d.has_response("delete")))
        ui(answer, "trash")
        wait(lambda: dialog() is not None and not page().busy, 30)
        ui(answer, "keep")
        ok = has == (True, False) and not os.path.exists(S("Downloads/photos (1).zip")) \
            and os.path.exists(S("Downloads/photos.zip"))
        return ok, f"buttons trash/delete={has}, duplicate trashed, original kept={ok}"

    @case("9. Trash: lists items with where they came from, Empty removes them for good")
    def _():
        open_cat("trash")
        it = items_of("trash")
        shot("09-trash")
        subs = [v.subtitle for v in it.values()]
        ui(page().confirm)
        time.sleep(0.4)
        d = ui(dialog)
        has = ui(lambda: d.has_response("clean"))
        ui(answer, "clean")
        wait(lambda: dialog() is not None and not page().busy, 30)
        head = ui(lambda: dialog().get_heading())
        ui(answer, "ok")
        empty = not os.listdir(os.path.join(core.TRASH, "files"))
        ok = "photos (1).zip" in it and "old-draft.txt" in it and has and empty and "Freed" in head
        return ok, f"items={list(it)}, from='{subs[0] if subs else ''}', emptied={empty}, result='{head}'"

    @case("10. Explore folders: sizes, drill-down and Move to Trash")
    def _():
        ui(lambda: win.nav.pop_to_tag("home"))
        ui(win.open_explorer, core.HOME)
        wait(lambda: page().stack.get_visible_child_name() == "list", 30)
        shot("10-explorer")
        titles = ui(lambda: [r.get_title() for r in page().rows])
        row = ui(lambda: next(r for r in page().rows if r.get_title() == "bigfolder"))
        ui(lambda: page().trash(row, "bigfolder", S("bigfolder"), 4 * MB))
        time.sleep(0.4)
        ui(answer, "trash")
        wait(lambda: not os.path.exists(S("bigfolder")), 10)
        time.sleep(0.5)
        gone = not os.path.exists(S("bigfolder")) and "bigfolder" not in ui(lambda: [r.get_title() for r in page().rows])
        protected = ui(lambda: [r.get_title() for r in page().rows if r.get_title() in ("Documents", "Downloads")])
        return gone and "Videos" in titles and len(protected) == 2, f"rows={titles[:8]}, trashed bigfolder={gone}"

    @case("11. Large files: finds big files (threshold lowered for the test)")
    def _():
        ui(lambda: setattr(win.cats["large"], "min_mb", 5))
        open_cat("large")
        ui(page().rescan)
        wait(lambda: not page().scanning(), 30)
        it = items_of("large")
        shot("11-large")
        return "big-video.mkv" in it and not it["big-video.mkv"].checked, list(it)

    @case("12. Docker and System pages explain themselves (no cleaning run)")
    def _():
        open_cat("docker")
        shot("12-docker")
        dn = ui(lambda: page().empty.get_title()) if not win.cats["docker"].items else "has items"
        open_cat("system")
        shot("13-system")
        sys_items = list(items_of("system"))
        return bool(dn), f"docker: {dn}; system items: {sys_items}"

    @case("13. Safety: refuses home, top folders, keys, Trash and system paths")
    def _():
        bad = [core.HOME, S("Documents"), S("Downloads"), S(".ssh/id_rsa"), "/etc/hosts", "/usr",
               os.path.join(core.TRASH, "files", "x"), S(".config"), REAL_HOME + "/Documents/x"]
        refused = {p: not core.is_safe(p) for p in bad}
        ok_paths = [S("Documents/projB/node_modules"), S(".cache/mystery-app")]
        allowed = {p: core.is_safe(p) for p in ok_paths}
        try:
            core.remove_path(S("Documents"), "delete")
            raised = False
        except PermissionError:
            raised = True
        # root commands: only allowlisted programs, arguments shell-quoted
        evil = core.Item("x", kind="root", cmd=[["snap", "remove", "a; rm -rf /", "--revision=1"]])
        script = core.root_script([evil])
        quoted = "'a; rm -rf /'" in script
        try:
            core.root_script([core.Item("y", kind="root", cmd=[["rm", "-rf", "/"]])])
            blocked = False
        except ValueError:
            blocked = True
        ok = all(refused.values()) and all(allowed.values()) and raised and os.path.isdir(S("Documents"))
        return ok and quoted and blocked, (f"refused={sum(refused.values())}/{len(bad)}, "
                                           f"allowed={sum(allowed.values())}/{len(ok_paths)}, "
                                           f"root args quoted={quoted}, unknown root program blocked={blocked}")

    @case("14. Recommended cleanup page and dark mode render")
    def _():
        ui(lambda: win.nav.pop_to_tag("home"))
        ui(lambda: win.nav.push(sc.CleanPage(win, quick=True)))
        time.sleep(0.5)
        shot("14-quick")
        n = ui(lambda: len(page().entries))
        ui(lambda: Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK))
        ui(lambda: win.nav.pop_to_tag("home"))
        shot("15-home-dark")
        ui(lambda: Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT))
        return n > 0, f"{n} recommended items"

    @case("15. Your real Trash was not touched")
    def _():
        after = sorted(os.listdir(REAL_TRASH)) if os.path.isdir(REAL_TRASH) else []
        return after == real_trash_before, f"{len(after)} items before and after"

    ui(win.close)
    GLib.idle_add(app.quit)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    threading.Thread(target=tests, daemon=True).start()
    try:
        app.run(None)
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
        with open(os.path.join(HERE, "last-results.json"), "w") as f:
            json.dump(results, f, indent=1)
        passed = sum(r["pass"] for r in results)
        print(f"\n{passed}/{len(results)} passed")
        sys.exit(0 if passed == len(results) and results else 1)


main()

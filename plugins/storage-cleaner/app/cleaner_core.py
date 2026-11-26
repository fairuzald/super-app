"""Storage Cleaner core: finds space that can be freed and removes it safely.

No GTK here, so it can be tested on its own. Set SC_HOME (and XDG_DATA_HOME)
to point the whole scanner at a fake home folder.
"""
import hashlib, json, os, re, shlex, shutil, stat, subprocess, sys, threading, time
from urllib.parse import unquote
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

HOME = os.path.realpath(os.environ.get("SC_HOME") or os.path.expanduser("~"))
if os.environ.get("SC_HOME"):
    DOWNLOADS = os.path.join(HOME, "Downloads")
else:
    DOWNLOADS = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.join(HOME, "Downloads")
DOWNLOADS = os.path.realpath(DOWNLOADS)
DATA_HOME = os.path.realpath(GLib.get_user_data_dir())
TRASH = os.path.join(DATA_HOME, "Trash")
CACHE = os.path.join(HOME, ".cache")
CONFIG = os.path.join(HOME, ".config")
DOCS = os.path.join(HOME, "Documents")
DAY = 86400
INACTIVE_DAYS = 30
MIN_SHOW = 1 << 20            # hide things smaller than 1 MB


def H(*p):
    return os.path.join(HOME, *p)


PROJECT_ROOTS = [DOCS] + [H(n) for n in ("projects", "Projects", "code", "dev", "src", "work", "git",
                                         "repos", "workspace", "Developer")]

# Folders that must never be removed themselves (their contents may be).
PROTECTED = {os.path.realpath(p) for p in (
    HOME, DOCS, DOWNLOADS, CACHE, CONFIG, TRASH, DATA_HOME, H(".local"), H(".local", "share"),
    os.path.join(TRASH, "files"), os.path.join(TRASH, "info"), H("Desktop"), H("Pictures"),
    H("Music"), H("Videos"), H("Templates"), H("Public"), H("snap"), H(".var"))}
# Folders whose contents are never touched (keys, passwords, credentials).
SENSITIVE = [os.path.realpath(p) for p in (
    H(".ssh"), H(".gnupg"), H(".password-store"), os.path.join(DATA_HOME, "keyrings"), H(".pki"),
    H(".aws"), H(".kube"), H(".docker"), H(".config", "gcloud"), H(".local", "share", "keyrings"))]


# ---------------------------------------------------------------- formatting
def fmt_size(n):
    return GLib.format_size(max(0, int(n)))


def fmt_age(ts, now=None):
    if not ts:
        return "unknown"
    d = ((now or time.time()) - ts) / DAY
    if d < 1:
        return "today"
    if d < 2:
        return "yesterday"
    if d < 30:
        return f"{int(d)} days ago"
    if d < 365:
        m = int(d // 30)
        return "1 month ago" if m == 1 else f"{m} months ago"
    y = int(d // 365)
    return "1 year ago" if y == 1 else f"{y} years ago"


def short(p):
    p = str(p)
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + os.sep) else p


def parse_size(s):
    """Docker-style human size ('1.2GB', '512kB', '0B') -> bytes (decimal units)."""
    m = re.match(r"\s*([\d.]+)\s*([kKMGTP]?i?B)?", s or "")
    if not m:
        return 0
    unit = (m.group(2) or "B").upper().replace("I", "")
    mult = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "PB": 1e15}.get(unit, 1)
    return int(float(m.group(1)) * mult)


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=60, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 255, "", str(e))


def du_sizes(paths):
    """Disk usage (bytes) of many paths, via one `du` call per 300 paths."""
    out = {}
    paths = list(paths)
    for i in range(0, len(paths), 300):
        chunk = paths[i:i + 300]
        try:
            r = subprocess.run(["du", "-s", "-B1", "-x", "-0", "--", *chunk],
                               capture_output=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for rec in r.stdout.split(b"\0"):
            if rec:
                sz, _, p = rec.partition(b"\t")
                try:
                    out[os.fsdecode(p)] = int(sz)
                except ValueError:
                    pass
    return out


def du_one(path):
    return du_sizes([path]).get(path, 0)


def file_size(st):
    return st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size


def disk_usage(path):
    try:
        s = os.statvfs(path)
    except OSError:
        return None
    total = s.f_blocks * s.f_frsize
    free = s.f_bavail * s.f_frsize
    return {"total": total, "free": free, "used": total - s.f_bfree * s.f_frsize,
            "dev": os.stat(path).st_dev}


def mtime(p):
    try:
        return os.lstat(p).st_mtime
    except OSError:
        return 0


# ---------------------------------------------------------------- safety
def resolve(p):
    """Absolute path with parent symlinks resolved but the last part kept (we remove links, not targets)."""
    a = os.path.abspath(p)
    return os.path.join(os.path.realpath(os.path.dirname(a)), os.path.basename(a))


def is_safe(p, allow_trash=False):
    r = resolve(p)
    if os.path.realpath(r) in PROTECTED or r in PROTECTED:
        return False
    if not r.startswith(HOME + os.sep):
        return False
    if any(r == s or r.startswith(s + os.sep) for s in SENSITIVE):
        return False
    in_trash = r.startswith(TRASH + os.sep)
    return in_trash == allow_trash


def _make_writable(func, path, _exc):
    """rmtree helper: read-only folders (e.g. Go modules) need write permission to empty them."""
    os.chmod(os.path.dirname(path), 0o700)
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, 0o700)
    func(path)


def remove_path(p, mode="trash", allow_trash=False):
    if not is_safe(p, allow_trash):
        raise PermissionError(f"Refusing to touch {short(p)}")
    if not os.path.lexists(p):
        return
    if mode == "trash":
        Gio.File.new_for_path(p).trash(None)
    elif os.path.isdir(p) and not os.path.islink(p):
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=_make_writable)
        else:
            shutil.rmtree(p, onerror=_make_writable)
    else:
        os.unlink(p)


# ---------------------------------------------------------------- data model
class Item:
    """One thing that can be cleaned.

    kind: "path"    - files/folders in home: moved to Trash or deleted
          "trashed" - entries already in the Trash: deleted for good
          "cmd"     - a command run as you (docker prune, go clean ...)
          "root"    - a shell command run as root through pkexec (one password prompt for all)
    """

    def __init__(self, title, size=0, paths=(), subtitle="", checked=False, group="", kind="path",
                 cmd=None, note="", thumb=False, warn=None, mtime=0):
        self.title, self.size, self.paths = title, int(size or 0), list(paths)
        self.subtitle, self.checked, self.group, self.kind = subtitle, checked, group, kind
        self.cmd, self.note, self.thumb, self.warn, self.mtime = cmd, note, thumb, warn, mtime

    def __repr__(self):
        return f"<Item {self.title!r} {fmt_size(self.size)} {self.kind} checked={self.checked}>"


class Category:
    id = title = icon = color = caption = what = ""
    safety = "safe"        # safe | review | careful
    method = "rebuild"     # rebuild: Trash or delete | files: Trash only | permanent: delete only
    needs_root = False
    group_order = ()

    def __init__(self):
        self.items, self.notice, self.error, self.scanned = [], None, None, False

    def scan(self, progress=None):
        raise NotImplementedError

    def refresh(self, progress=None):
        self.notice = self.error = None
        try:
            self.items = [i for i in self.scan(progress) if i.size >= MIN_SHOW or i.kind != "path"]
        except Exception as e:      # a scanner failing must never take the app down
            self.items, self.error = [], str(e)
        self.items.sort(key=lambda i: -i.size)
        self.scanned = True
        return self.items

    @property
    def total(self):
        return sum(i.size for i in self.items)

    @property
    def recommended(self):
        return sum(i.size for i in self.items if i.checked)


# ---------------------------------------------------------------- caches (~/.cache)
CACHE_INFO = {
    "pip": ("pip downloads (Python)", True, ""),
    "pypoetry": ("Poetry downloads (Python)", True, ""),
    "uv": ("uv downloads (Python)", True, ""),
    "yarn": ("Yarn package downloads", True, "Yarn downloads packages again when needed"),
    "pnpm": ("pnpm metadata", True, ""),
    "node-gyp": ("Node.js native build headers", True, ""),
    "go-build": ("Go build cache", True, ""),
    "thumbnails": ("Picture thumbnails", True, "Recreated when you open folders"),
    "google-chrome": ("Chrome browser cache", True, "Close Chrome first for best results"),
    "chromium": ("Chromium browser cache", True, "Close the browser first"),
    "BraveSoftware": ("Brave browser cache", True, "Close the browser first"),
    "microsoft-edge": ("Edge browser cache", True, "Close the browser first"),
    "vivaldi": ("Vivaldi browser cache", True, "Close the browser first"),
    "mozilla": ("Firefox browser cache", True, "Close Firefox first"),
    "zen": ("Zen browser cache", True, "Close the browser first"),
    "mesa_shader_cache": ("Graphics shader cache", True, ""),
    "mesa_shader_cache_db": ("Graphics shader cache", True, ""),
    "fontconfig": ("Font cache", True, ""),
    "flatpak": ("Flatpak download cache", True, ""),
    "black": ("Black formatter cache", True, ""),
    "matplotlib": ("Matplotlib font cache", True, ""),
    "pip-audit": ("pip-audit cache", True, ""),
    "helm": ("Helm chart cache", True, ""),
    "gstreamer-1.0": ("GStreamer plugin cache", True, ""),
    "tableplus": ("TablePlus cache", True, ""),
    "anytype-updater": ("Anytype old updates", True, ""),
    "tracker3": ("GNOME search index", False, "Rebuilt in the background; file search is slower for a while"),
    "huggingface": ("Hugging Face AI models", False, "Large AI models: downloaded again when an app needs them"),
    "torch": ("PyTorch models", False, "Downloaded again when a script needs them"),
    "ms-playwright": ("Playwright test browsers", False, "Get them back with “npx playwright install”"),
    "ms-playwright-mcp": ("Playwright browsers (MCP)", False, "Downloaded again when needed"),
    "puppeteer": ("Puppeteer browser", False, "Downloaded again when Puppeteer runs"),
}
TOOL_CACHE_NOTE = "Used by a developer tool, which downloads what it needs again. Close the tool first."
TOOL_CACHES = {"codex-runtimes", "Codex", "claude-cli-nodejs", "opencode", "copilot", "dotslash", "eas-cli",
               "com.vercel.cli", "go", "node", "JNA", "Microsoft", "Google", "cursor", "jetbrains", "JetBrains"}
SKIP_CACHES = {"storage-cleaner", "phone-connect", "dconf", "ibus", "ibus-table", "gnome-desktop-thumbnailer",
               "evolution", "obexd", "gitstatus"}


class AppCaches(Category):
    id, icon, color = "caches", "folder-download-symbolic", "c-blue"
    title = "App caches"
    caption = "Temporary files apps keep to load faster"
    what = ("Apps save copies of things they downloaded (packages, web pages, thumbnails) so they open faster "
            "next time. Removing them is safe: apps simply make them again when needed.")
    safety, method = "safe", "rebuild"
    group_order = ("Safe to remove", "Big downloads apps will fetch again", "Other app caches")

    def scan(self, progress=None):
        try:
            ents = [e for e in os.scandir(CACHE) if e.name not in SKIP_CACHES and not e.name.startswith("p10k")]
        except OSError:
            return []
        sizes = du_sizes([e.path for e in ents])
        items = []
        for e in ents:
            label, checked, note = CACHE_INFO.get(e.name, (None, False, ""))
            if label:
                group = "Safe to remove" if checked else "Big downloads apps will fetch again"
            elif e.name in TOOL_CACHES:
                label, note, group = f"{e.name} (developer tool)", TOOL_CACHE_NOTE, "Other app caches"
            else:
                label, note, group = f"{e.name}", "An app’s cache; the app rebuilds it when needed", "Other app caches"
            items.append(Item(label, sizes.get(e.path, 0), [e.path], subtitle=short(e.path), checked=checked,
                              group=group, note=note, mtime=mtime(e.path)))
        return items


# ---------------------------------------------------------------- developer caches outside ~/.cache
DEV_CACHES = [   # (paths, title, checked, note, group)
    ([".npm/_cacache"], "npm package downloads", True, "npm downloads packages again when needed", "Package & build caches"),
    ([".npm/_npx"], "npx temporary packages", True, "", "Package & build caches"),
    ([".yarn/berry/cache"], "Yarn 2+ package downloads", True, "", "Package & build caches"),
    ([".gradle/caches"], "Gradle downloads & build cache", True, "Android/Java builds download dependencies again", "Package & build caches"),
    ([".gradle/daemon"], "Gradle daemon logs", True, "", "Package & build caches"),
    ([".cargo/registry"], "Rust crate downloads", True, "", "Package & build caches"),
    ([".nvm/.cache"], "nvm download cache", True, "", "Package & build caches"),
    ([".android/cache"], "Android tools cache", True, "", "Package & build caches"),
    ([".local/share/pnpm/store"], "pnpm package store", False, "Shared by your projects; pnpm re-downloads missing packages", "Package & build caches"),
    ([".gradle/wrapper/dists"], "Downloaded Gradle versions", False, "Projects download the version they need again", "Old versions"),
    ([".m2/repository"], "Maven packages", False, "Java builds download them again", "Package & build caches"),
    ([".config/Code/Cache", ".config/Code/CachedData", ".config/Code/CachedExtensionVSIXs",
      ".config/Code/Code Cache", ".config/Code/GPUCache", ".config/Code/logs"],
     "VS Code caches & logs", True, "Close VS Code first", "Editors"),
    ([".config/Cursor/Cache", ".config/Cursor/CachedData", ".config/Cursor/CachedExtensionVSIXs",
      ".config/Cursor/Code Cache", ".config/Cursor/GPUCache", ".config/Cursor/logs"],
     "Cursor caches & logs", True, "Close Cursor first", "Editors"),
]


def _ver_key(v):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v.lstrip("v"))]


def old_vscode_extensions(ext_dir):
    """Extension folders VS Code no longer uses: listed in .obsolete, or older versions of a newer one."""
    try:
        names = [e.name for e in os.scandir(ext_dir) if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        return []
    old = set()
    try:
        with open(os.path.join(ext_dir, ".obsolete")) as f:
            old |= {k for k, v in json.load(f).items() if v}
    except (OSError, ValueError, AttributeError):
        pass
    by_id = {}
    for n in names:
        m = re.match(r"^(.+?)-(\d+\.\d+\.\d+.*)$", n)
        if m:
            by_id.setdefault(m.group(1).lower(), []).append((_ver_key(m.group(2)), n))
    for vers in by_id.values():
        vers.sort()
        old |= {n for _, n in vers[:-1]}
    return [os.path.join(ext_dir, n) for n in sorted(old) if n in names]


def old_node_versions(nvm_dir):
    vdir = os.path.join(nvm_dir, "versions", "node")
    try:
        vers = sorted((e.name for e in os.scandir(vdir) if e.name.startswith("v")), key=_ver_key)
    except OSError:
        return []
    if len(vers) < 2:
        return []
    keep = {vers[-1]}
    try:
        with open(os.path.join(nvm_dir, "alias", "default")) as f:
            want = f.read().strip()
        match = [v for v in vers if v.lstrip("v").startswith(want.lstrip("v"))] if want[:1].isdigit() or want[:1] == "v" else []
        if match:
            keep.add(match[-1])
    except OSError:
        pass
    return [os.path.join(vdir, v) for v in vers if v not in keep]


class DevCaches(Category):
    id, icon, color = "devcaches", "applications-engineering-symbolic", "c-purple"
    title = "Developer caches"
    caption = "npm, Gradle, Rust, VS Code and old tool versions"
    what = ("Package managers and editors keep downloaded packages and caches in your home folder. "
            "They are fetched again when a project needs them, so removing them is safe; the next build "
            "may just take longer.")
    safety, method = "safe", "rebuild"
    group_order = ("Package & build caches", "Editors", "Old versions", "Android")

    def scan(self, progress=None):
        items, want = [], []
        for rels, title, checked, note, group in DEV_CACHES:
            paths = [H(r) for r in rels if os.path.isdir(H(r))]
            if paths:
                want.append((paths, title, checked, note, group))
        exts = old_vscode_extensions(H(".vscode", "extensions"))
        nodes = old_node_versions(H(".nvm"))
        sysimg = []
        for base in (H("Android", "Sdk", "system-images"),):
            for root, dirs, _f in os.walk(base):
                if os.path.exists(os.path.join(root, "source.properties")) or os.path.exists(os.path.join(root, "system.img")):
                    sysimg.append(root)
                    dirs.clear()
                elif root.count(os.sep) - base.count(os.sep) >= 3:
                    dirs.clear()
        allp = [p for w in want for p in w[0]] + exts + nodes + sysimg
        sizes = du_sizes(allp)
        for paths, title, checked, note, group in want:
            items.append(Item(title, sum(sizes.get(p, 0) for p in paths), paths, subtitle=short(paths[0]),
                              checked=checked, group=group, note=note))
        if exts:
            items.append(Item(f"Old VS Code extension versions ({len(exts)})", sum(sizes.get(p, 0) for p in exts),
                              exts, subtitle="Replaced by newer versions", checked=True, group="Old versions",
                              note="VS Code already uses the newer copy"))
        for p in nodes:
            items.append(Item(f"Node.js {os.path.basename(p)} (nvm)", sizes.get(p, 0), [p], subtitle=short(p),
                              group="Old versions", note="Not your default Node version. Reinstall with “nvm install” if a project needs it"))
        if os.path.isdir(H("go", "pkg", "mod")) and shutil.which("go"):
            items.append(Item("Go module downloads", du_one(H("go", "pkg", "mod")), kind="cmd",
                              cmd=["go", "clean", "-modcache"], subtitle="~/go/pkg/mod  ·  runs “go clean -modcache”",
                              checked=False, group="Package & build caches", note="Go downloads modules again"))
        for p in sysimg:
            rel = os.path.relpath(p, H("Android", "Sdk", "system-images"))
            items.append(Item(f"Android emulator image: {rel}", sizes.get(p, 0), [p], subtitle=short(p),
                              group="Android", note="Re-download it in Android Studio’s SDK Manager if an emulator needs it"))
        return items


# ---------------------------------------------------------------- projects (node_modules, venvs, build output)
JUNK = {   # folder name: (label, required marker files in the parent or None, kind)
    ".next": ("Next.js build", None, "build"),
    ".nuxt": ("Nuxt build", None, "build"),
    ".svelte-kit": ("SvelteKit build", None, "build"),
    ".turbo": ("Turborepo cache", None, "cache"),
    ".parcel-cache": ("Parcel cache", None, "cache"),
    ".angular": ("Angular cache", None, "cache"),
    ".pytest_cache": ("Python test cache", None, "cache"),
    ".mypy_cache": ("mypy cache", None, "cache"),
    ".ruff_cache": ("Ruff cache", None, "cache"),
    "__pycache__": ("Python bytecode", None, "cache"),
    ".tox": ("tox environments", None, "build"),
    ".nox": ("nox environments", None, "build"),
    "target": ("Rust build", ("Cargo.toml",), "build"),
    "build": ("Build output", ("package.json", "pyproject.toml", "setup.py", "build.gradle", "build.gradle.kts",
                               "CMakeLists.txt", "pubspec.yaml"), "build"),
    "dist": ("Build output", ("package.json", "pyproject.toml", "setup.py"), "build"),
    ".gradle": ("Gradle project cache", ("build.gradle", "build.gradle.kts", "settings.gradle",
                                         "settings.gradle.kts", "gradlew"), "cache"),
    ".dart_tool": ("Dart tool cache", ("pubspec.yaml",), "cache"),
}
MARKERS = {".git", "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod",
           "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "pubspec.yaml", "composer.json"}


class ProjectIndex:
    """One shared walk of the project folders, used by the node_modules and build categories."""
    lock = threading.Lock()
    found, when = None, 0

    @classmethod
    def get(cls, force=False):
        with cls.lock:
            if force or cls.found is None or time.time() - cls.when > 60:
                cls.found, cls.when = walk_projects([r for r in PROJECT_ROOTS if os.path.isdir(r)]), time.time()
            return cls.found


def walk_projects(roots, max_depth=10):
    found = []        # (path, kind) kind: "node", "venv" or a JUNK name
    seen = set()
    stack = [(os.path.realpath(r), 0) for r in roots]
    while stack:
        d, depth = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        names = {e.name for e in entries}
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            n = e.name
            if n == "node_modules":
                found.append((e.path, "node"))
                continue
            if n in (".git", ".hg", ".svn", ".idea", ".vscode"):
                continue
            j = JUNK.get(n)
            if j and (j[1] is None or names & set(j[1])):
                found.append((e.path, n))
                continue
            if os.path.exists(os.path.join(e.path, "pyvenv.cfg")):
                found.append((e.path, "venv"))
                continue
            if n.startswith(".") or depth >= max_depth:
                continue
            stack.append((e.path, depth + 1))
    return found


def project_of(path, roots=None):
    """Nearest folder above `path` that looks like a project (has package.json, .git ...)."""
    roots = {os.path.realpath(r) for r in (roots or PROJECT_ROOTS)}
    d = os.path.dirname(path)
    first = d
    while d and d not in roots and d != HOME and d != os.sep:
        try:
            if MARKERS & set(os.listdir(d)):
                return d
        except OSError:
            pass
        d = os.path.dirname(d)
    return first


def git_root(d):
    while d and d != HOME and d != os.sep:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def last_activity(project):
    """When you last worked on a project: newest of git activity and its top-level entries."""
    times = []
    g = git_root(project)
    if g:
        for f in ("index", "HEAD", "FETCH_HEAD", "logs/HEAD", "ORIG_HEAD"):
            times.append(mtime(os.path.join(g, ".git", f)))
    try:
        for e in os.scandir(project):
            if e.name not in JUNK and e.name not in ("node_modules", ".git") and not os.path.exists(
                    os.path.join(e.path, "pyvenv.cfg")):
                try:
                    times.append(e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return max(times) if times else 0


def project_name(p):
    for r in PROJECT_ROOTS:
        r = os.path.realpath(r)
        if p.startswith(r + os.sep):
            return os.path.relpath(p, r)
    return short(p)


class NodeModules(Category):
    id, icon, color = "node", "package-x-generic-symbolic", "c-green"
    title = "node_modules"
    caption = "Installed JavaScript packages in your projects"
    what = ("Every JavaScript project keeps its own copy of its packages in a “node_modules” folder. "
            "It is rebuilt by running “npm install” (or yarn / pnpm), so it is safe to remove from projects "
            "you are not working on right now.")
    safety, method = "safe", "rebuild"
    group_order = (f"Not touched in {INACTIVE_DAYS}+ days", "Recently used projects")

    def scan(self, progress=None, force=False):
        found = [p for p, k in ProjectIndex.get(force) if k == "node"]
        sizes = du_sizes(found)
        now, items = time.time(), []
        for p in found:
            proj = os.path.dirname(p)
            act = last_activity(project_of(p) if not os.path.exists(os.path.join(proj, "package.json")) else proj)
            old = now - act > INACTIVE_DAYS * DAY
            items.append(Item(project_name(proj), sizes.get(p, 0), [p], checked=old, mtime=act,
                              subtitle=f"Last worked on {fmt_age(act)}  ·  {short(p)}",
                              group=self.group_order[0 if old else 1],
                              note="Run “npm install” in the project to get it back"))
        return items


class BuildLeftovers(Category):
    id, icon, color = "build", "applications-science-symbolic", "c-yellow"
    title = "Python & build leftovers"
    caption = "venvs, __pycache__, dist/build, .next, Rust target"
    what = ("Projects create helper folders while you develop: Python virtual environments (.venv), "
            "bytecode caches (__pycache__), and build output (dist, build, .next, target). All can be "
            "recreated by building or installing again.")
    safety, method = "safe", "rebuild"
    group_order = ("Caches (always safe)", "Build output", "Python virtual environments")

    def scan(self, progress=None):
        found = [(p, k) for p, k in ProjectIndex.get() if k != "node"]
        sizes = du_sizes([p for p, _ in found])
        groups = {}
        for p, k in found:
            proj = project_of(p)
            if k == "venv":
                label, kind = f"Virtual environment ({os.path.basename(p)})", "venv"
            else:
                label, _m, kind = JUNK[k]
            groups.setdefault((proj, label, kind), []).append(p)
        now, items, acts = time.time(), [], {}
        for (proj, label, kind), paths in groups.items():
            act = acts.get(proj) or acts.setdefault(proj, last_activity(proj))
            old = now - act > INACTIVE_DAYS * DAY
            n = f" ×{len(paths)}" if len(paths) > 1 else ""
            if kind == "cache":
                group, checked, note = "Caches (always safe)", True, "Recreated automatically"
            elif kind == "venv":
                group, checked = "Python virtual environments", old
                note = "Recreate with “python -m venv” and install the requirements again"
            else:
                group, checked, note = "Build output", old, "Recreated next time you build"
            sub = short(paths[0]) if len(paths) == 1 else f"{len(paths)} folders in {short(proj)}"
            items.append(Item(f"{project_name(proj)}  ·  {label}{n}", sum(sizes.get(p, 0) for p in paths), paths,
                              subtitle=f"Last worked on {fmt_age(act)}  ·  {sub}", checked=checked, group=group,
                              note=note, mtime=act))
        return items


# ---------------------------------------------------------------- Trash
def read_trashinfo(info_path):
    orig, when = None, 0
    try:
        with open(info_path, errors="replace") as f:
            for line in f:
                if line.startswith("Path="):
                    orig = unquote(line[5:].strip())
                elif line.startswith("DeletionDate="):
                    try:
                        when = time.mktime(time.strptime(line[13:].strip()[:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:
                        pass
    except OSError:
        pass
    return orig, when


def trash_entries():
    """[(name, original path, deleted at)] for everything in the home Trash."""
    files, info = os.path.join(TRASH, "files"), os.path.join(TRASH, "info")
    try:
        names = os.listdir(files)
    except OSError:
        return []
    out = []
    for n in names:
        orig, when = read_trashinfo(os.path.join(info, n + ".trashinfo"))
        if orig and not orig.startswith("/"):
            orig = os.path.join(HOME, orig)
        out.append((n, orig, when))
    return out


def trash_items(entries=None):
    entries = trash_entries() if entries is None else entries
    files, info = os.path.join(TRASH, "files"), os.path.join(TRASH, "info")
    sizes = du_sizes([os.path.join(files, n) for n, _o, _w in entries])
    items = []
    for n, orig, when in entries:
        fp = os.path.join(files, n)
        title = os.path.basename(orig) if orig else n
        where = short(os.path.dirname(orig)) if orig else "unknown place"
        items.append(Item(title, sizes.get(fp, 0), [fp, os.path.join(info, n + ".trashinfo")], kind="trashed",
                          subtitle=f"From {where}  ·  deleted {fmt_age(when)}", checked=True,
                          group="In your Trash", thumb=True, mtime=when))
    return items


def find_trashed(originals):
    """Trash entries whose original path is one of `originals` (to empty just what we trashed)."""
    want = {resolve(p) for p in originals}
    return [e for e in trash_entries() if e[1] and resolve(e[1]) in want]


class Trash(Category):
    id, icon, color = "trash", "user-trash-symbolic", "c-red"
    title = "Trash"
    caption = "Files you already deleted"
    what = ("Files you delete go to the Trash first and still use space until the Trash is emptied. "
            "Emptying removes them for good, so check nothing here is still needed.")
    safety, method = "careful", "permanent"
    group_order = ("In your Trash", "Leftovers")

    def scan(self, progress=None):
        items = trash_items()
        exp = os.path.join(TRASH, "expunged")
        try:
            left = [os.path.join(exp, n) for n in os.listdir(exp)]
        except OSError:
            left = []
        if left:
            items.append(Item("Half-deleted leftovers", sum(du_sizes(left).values()), left, kind="trashed",
                              checked=True, group="Leftovers", subtitle="From earlier Trash emptying"))
        return items


# ---------------------------------------------------------------- Downloads
FILE_TYPES = [
    ("Installers", {".deb", ".rpm", ".appimage", ".run", ".exe", ".msi", ".dmg", ".iso", ".pkg", ".snap",
                    ".flatpak", ".flatpakref", ".apk", ".aab", ".img", ".vsix", ".xpi"}),
    ("Archives", {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".tbz2", ".txz", ".lz"}),
    ("Images", {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".heif", ".tif", ".tiff",
                ".avif", ".raw", ".psd", ".ico"}),
    ("Videos", {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".3gp", ".mpg", ".mpeg"}),
    ("Music & audio", {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"}),
    ("Documents", {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".txt",
                   ".csv", ".md", ".epub", ".rtf", ".json", ".html", ".xml", ".sql"}),
]
PARTIAL = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp"}
COPY_RE = re.compile(r"( \(\d+\)|\(\d+\)| - Copy| copy|-\d)$", re.I)


def ext_of(name):
    n = name.lower()
    for multi in (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"):
        if n.endswith(multi):
            return multi, name[:-len(multi)]
    stem, ext = os.path.splitext(name)
    return ext.lower(), stem


def type_of(ext):
    if ext.startswith(".tar."):
        return "Archives"
    for name, exts in FILE_TYPES:
        if ext in exts:
            return name
    return "Other files"


def quick_hash(p, size):
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    try:
        with open(p, "rb") as f:
            if size <= 64 << 20:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            else:                       # big files: start, middle, end
                for off in (0, size // 2, max(0, size - (4 << 20))):
                    f.seek(off)
                    h.update(f.read(4 << 20))
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(files):
    """files: [(path, size, mtime)] -> {path_of_extra_copy: path_kept}"""
    by_size = {}
    for f in files:
        if f[1] > 0:
            by_size.setdefault(f[1], []).append(f)
    dups = {}
    for same in by_size.values():
        if len(same) < 2:
            continue
        by_hash = {}
        for f in same:
            h = quick_hash(f[0], f[1])
            if h:
                by_hash.setdefault(h, []).append(f)
        for copies in by_hash.values():
            if len(copies) < 2:
                continue
            # keep the copy without "(1)"/"copy" in its name, then the oldest one
            copies.sort(key=lambda f: (bool(COPY_RE.search(ext_of(os.path.basename(f[0]))[1])), f[2]))
            for f in copies[1:]:
                dups[f[0]] = copies[0][0]
    return dups


def deb_installed(path):
    r = run(["dpkg-deb", "-f", path, "Package"], timeout=10)
    pkg = r.stdout.strip()
    if r.returncode or not pkg:
        return None
    q = run(["dpkg-query", "-W", "-f=${Status} ${Version}", pkg], timeout=10)
    if q.returncode == 0 and "install ok installed" in q.stdout:
        return q.stdout.split()[-1]
    return None


def newest_in_tree(d, limit=20000):
    newest, n = mtime(d), 0
    for root, dirs, files in os.walk(d):
        for f in files + dirs:
            newest = max(newest, mtime(os.path.join(root, f)))
            n += 1
        if n > limit:
            break
    return newest


class Downloads(Category):
    id, icon, color = "downloads", "folder-download-symbolic", "c-teal"
    title = "Old downloads"
    caption = "Old installers, archives, pictures and duplicates"
    what = ("Things you downloaded and probably don’t need anymore: old installers, zip files you already "
            "unzipped, duplicate copies, unfinished downloads, and old pictures, videos and documents. "
            "Items go to the Trash, so you can still get them back.")
    safety, method = "review", "files"
    group_order = ("Unfinished downloads", "Duplicates", "Installers", "Archives", "Images", "Videos",
                   "Music & audio", "Documents", "Folders", "Other files")
    days = 90

    def scan(self, progress=None):
        now = time.time()
        try:
            ents = [e for e in os.scandir(DOWNLOADS) if not e.name.startswith(".")]
        except OSError:
            return []
        files, dirs = [], []
        for e in ents:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                files.append((e.path, file_size(st), st.st_mtime))
            elif stat.S_ISDIR(st.st_mode):
                dirs.append(e.path)
        dups = find_duplicates(files)
        names = {os.path.basename(p) for p in dirs}
        items = []
        for p, size, mt in files:
            name = os.path.basename(p)
            ext, stem = ext_of(name)
            kind = type_of(ext)
            old = now - mt > self.days * DAY
            sub = f"Downloaded {fmt_age(mt)}"
            note, checked, group = "", False, kind
            if ext in PARTIAL and now - mt > DAY:
                group, checked, note = "Unfinished downloads", True, "This download never finished"
            elif p in dups:
                group, checked = "Duplicates", True
                note = f"Same as “{os.path.basename(dups[p])}”, which is kept"
            elif kind == "Installers" and ext == ".deb" and (ver := deb_installed(p)):
                checked, note = True, f"Already installed (version {ver})"
            elif kind == "Archives" and stem in names:
                checked, note = True, "Already unzipped next to it"
            elif not old:
                continue
            elif kind in ("Installers", "Archives"):
                checked, note = True, "You can download it again if needed"
            items.append(Item(name, size, [p], subtitle=sub, checked=checked, group=group, note=note,
                              thumb=True, mtime=mt))
        dsizes = du_sizes(dirs)
        for d in dirs:
            mt = newest_in_tree(d)
            if now - mt > self.days * DAY:
                items.append(Item(os.path.basename(d), dsizes.get(d, 0), [d], subtitle=f"Last changed {fmt_age(mt)}",
                                  group="Folders", thumb=True, mtime=mt))
        return items


# ---------------------------------------------------------------- large files
class LargeFiles(Category):
    id, icon, color = "large", "drive-harddisk-symbolic", "c-orange"
    title = "Large files"
    caption = "Single files bigger than 500 MB"
    what = ("The biggest single files in your home folder, such as videos, disk images and backups. "
            "Nothing is ticked: pick only what you are sure about. Files go to the Trash.")
    safety, method = "review", "files"
    group_order = ("Your files", "Inside app folders (be careful)")
    min_mb = 500

    def scan(self, progress=None):
        cmd = ["find", HOME, "-xdev", "(", "-path", TRASH, "-o", "-name", "node_modules", "-o", "-name", ".git",
               "-o", "-path", H("snap"), ")", "-prune", "-o", "-type", "f", "-size", f"+{self.min_mb}M",
               "-printf", r"%s\t%T@\t%p\0"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        items = []
        for rec in out.split(b"\0"):
            if not rec:
                continue
            try:
                sz, mt, p = rec.split(b"\t", 2)
                sz, mt, p = int(sz), float(mt), os.fsdecode(p)
            except ValueError:
                continue
            rel = os.path.relpath(p, HOME).split(os.sep)
            app = any(part.startswith(".") for part in rel[:-1]) or rel[0] in ("Android", "snap", "go")
            items.append(Item(os.path.basename(p), sz, [p], subtitle=f"{short(os.path.dirname(p))}  ·  changed {fmt_age(mt)}",
                              group=self.group_order[1 if app else 0], thumb=True, mtime=mt,
                              note="Used by an app; removing it may reset or break that app" if app else ""))
        return items


# ---------------------------------------------------------------- Docker
class Docker(Category):
    id, icon, color = "docker", "network-server-symbolic", "c-blue"
    title = "Docker"
    caption = "Unused images, stopped containers, build cache"
    what = ("Docker keeps old images, stopped containers and build cache around. Removing unused ones is safe: "
            "running containers are never touched and images are pulled again when needed. "
            "Volumes can hold database data, so they are never ticked for you.")
    safety, method = "review", "permanent"
    group_order = ("Safe to remove", "Check first")

    def scan(self, progress=None):
        if not shutil.which("docker"):
            self.notice = ("Docker isn’t installed", "Nothing to clean here.", None)
            return []
        info = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
        if info.returncode:
            msg = (info.stderr or "").lower()
            if "permission denied" in msg:
                self.notice = ("No permission to use Docker", "Add yourself to the “docker” group, then log in again.", None)
            else:
                self.notice = ("Docker isn’t running", "Start it to see how much space it uses. You’ll be asked for your password.", "start")
            return []
        df = {}
        r = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=120)
        for line in r.stdout.splitlines():
            try:
                d = json.loads(line)
                df[d.get("Type")] = d
            except ValueError:
                pass

        def reclaim(t):
            return parse_size((df.get(t) or {}).get("Reclaimable", "0B"))

        items = []
        # Docker's own "dangling" and "reclaimable" numbers also count images that running containers use when
        # those images were pulled by digest (no tag), e.g. kind's kindest/node. Prune never removes those, so
        # leave out every image a container uses, and add up the rest ourselves.
        used = set()
        cids = run(["docker", "ps", "-aq", "--no-trunc"], timeout=60).stdout.split()
        if cids:
            used = set(run(["docker", "inspect", "--format", "{{.Image}}", *cids], timeout=60).stdout.split())

        def images(*flt):
            out = {}
            for line in run(["docker", "images", "-a", "--no-trunc", *flt, "--format", "{{.ID}} {{.Size}}"],
                            timeout=60).stdout.splitlines():
                iid, _, size = line.partition(" ")
                if iid and iid not in used:
                    out[iid] = parse_size(size)
            return out
        dang = images("-f", "dangling=true")
        if dang:
            items.append(Item(f"Untagged leftover images ({len(dang)})", sum(dang.values()), kind="cmd",
                              cmd=["docker", "image", "prune", "-f"], checked=True, group="Safe to remove",
                              subtitle="Old layers left behind after rebuilding images"))
        stopped = run(["docker", "ps", "-a", "-f", "status=exited", "-f", "status=created", "-f", "status=dead",
                       "--format", "{{.Names}}"], timeout=60).stdout.split()
        if stopped:
            items.append(Item(f"Stopped containers ({len(stopped)})", reclaim("Containers"), kind="cmd",
                              cmd=["docker", "container", "prune", "-f"], checked=True, group="Safe to remove",
                              subtitle=", ".join(stopped[:6]) + ("…" if len(stopped) > 6 else "")))
        if reclaim("Build Cache"):
            items.append(Item("Build cache", reclaim("Build Cache"), kind="cmd", cmd=["docker", "builder", "prune", "-f"],
                              checked=True, group="Safe to remove", subtitle="Makes the next “docker build” slower once"))
        unused = {k: v for k, v in images().items() if k not in dang}
        if unused:
            items.append(Item(f"Images no container uses ({len(unused)})", sum(unused.values()), kind="cmd",
                              cmd=["docker", "image", "prune", "-a", "-f"], group="Check first",
                              subtitle="Pulled or built again the next time you need them"))
        if reclaim("Local Volumes"):
            items.append(Item("Unused volumes", reclaim("Local Volumes"), kind="cmd",
                              cmd=["docker", "volume", "prune", "-a", "-f"], group="Check first",
                              subtitle="Data of removed containers (for example databases)",
                              warn="Volumes can contain database data and uploads. This can’t be undone."))
        return items


def start_docker():
    return run(["pkexec", "systemctl", "start", "docker.socket", "docker.service"], timeout=300)


# ---------------------------------------------------------------- system (root via pkexec)
class System(Category):
    id, icon, color = "system", "emblem-system-symbolic", "c-pink"
    title = "System cleanup"
    caption = "Package cache, old snaps, logs (asks for password)"
    what = ("Ubuntu keeps downloaded update files, older versions of snap apps, and system logs. "
            "Removing them is safe. Because they belong to the system, you’ll type your password once in "
            "the system dialog.")
    safety, method, needs_root = "safe", "permanent", True
    group_order = ("Safe to remove", "Check first")

    def scan(self, progress=None):
        items = []
        apt = du_one("/var/cache/apt/archives") if os.path.isdir("/var/cache/apt/archives") else 0
        if apt > MIN_SHOW:
            items.append(Item("Downloaded update files (apt)", apt, kind="root", cmd=[["apt-get", "clean"]], checked=True,
                              group="Safe to remove", subtitle="/var/cache/apt  ·  already installed"))
        if shutil.which("snap"):
            r = run(["snap", "list", "--all"], timeout=30)
            old = []
            for line in r.stdout.splitlines()[1:]:
                f = line.split()
                # names/revisions go into a root command: accept only what snap itself allows
                if len(f) >= 3 and "disabled" in line and SNAP_NAME.match(f[0]) and f[2].isdigit():
                    old.append((f[0], f[2]))
            if old:
                size = sum((os.path.getsize(p) if os.path.exists(p) else 0)
                           for p in (f"/var/lib/snapd/snaps/{n}_{rev}.snap" for n, rev in old))
                names = sorted({n for n, _ in old})
                items.append(Item(f"Old snap app versions ({len(old)})", size, kind="root", checked=True,
                                  cmd=[["snap", "remove", n, f"--revision={rev}"] for n, rev in old],
                                  group="Safe to remove", subtitle=", ".join(names[:6]) + ("…" if len(names) > 6 else ""),
                                  note="The current version of each app stays installed"))
        r = run(["journalctl", "--disk-usage"], timeout=30)
        m = re.search(r"take up ([\d.]+)\s*([KMGT]?)", r.stdout + r.stderr)
        if m:
            used = float(m.group(1)) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m.group(2)]
            if used > 300 << 20:
                items.append(Item("Old system logs", used - (200 << 20), kind="root", checked=True,
                                  cmd=[["journalctl", "--vacuum-size=200M"]], group="Safe to remove",
                                  subtitle=f"Logs use {fmt_size(used)}; the newest 200 MB are kept"))
        try:
            crashes = [os.path.join("/var/crash", n) for n in os.listdir("/var/crash") if n.endswith(".crash")]
        except OSError:
            crashes = []
        if crashes:
            items.append(Item(f"Crash reports ({len(crashes)})", sum(os.path.getsize(p) for p in crashes),
                              kind="root", cmd=[["find", "/var/crash", "-maxdepth", "1", "-type", "f", "-name", "*.crash", "-delete"]], checked=True, group="Safe to remove",
                              subtitle="/var/crash"))
        if shutil.which("apt-get"):
            r = run(["apt-get", "-s", "autoremove"], timeout=60)
            pkgs = re.findall(r"^Remv (\S+)", r.stdout, re.M)
            if pkgs:
                q = run(["dpkg-query", "-W", "-f=${Installed-Size}\n", *pkgs], timeout=30)
                size = sum(int(x) for x in q.stdout.split() if x.isdigit()) * 1024
                items.append(Item(f"Packages nothing needs anymore ({len(pkgs)})", size, kind="root",
                                  cmd=[["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y", "autoremove"]], group="Check first",
                                  subtitle=", ".join(pkgs[:6]) + ("…" if len(pkgs) > 6 else ""),
                                  note="Usually old kernels and leftovers of removed apps"))
        return items


CATEGORIES = [AppCaches, DevCaches, Trash, NodeModules, BuildLeftovers, Docker, Downloads, LargeFiles, System]


# ---------------------------------------------------------------- cleaning
def clean_items(items, mode="trash", progress=None):
    """Remove the given items. mode applies to "path" items: "trash" or "delete".

    Returns dict(freed=bytes, trashed=bytes, errors=[(title, message)], trashed_paths=[...]).
    """
    res = {"freed": 0, "trashed": 0, "errors": [], "trashed_paths": []}
    user = [i for i in items if i.kind != "root"]
    root = [i for i in items if i.kind == "root"]
    total = len(user) + (1 if root else 0)
    for n, it in enumerate(user):
        if progress:
            progress(n, total, it.title)
        try:
            if it.kind == "path":
                for p in it.paths:
                    remove_path(p, mode)
                if mode == "trash":
                    res["trashed"] += it.size
                    res["trashed_paths"] += it.paths
                else:
                    res["freed"] += it.size
            elif it.kind == "trashed":
                for p in it.paths:
                    remove_path(p, "delete", allow_trash=True)
                res["freed"] += it.size
            elif it.kind == "cmd":
                r = run(it.cmd, timeout=3600)
                if r.returncode:
                    raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or ["failed"])
                # docker prints what it really removed; trust that over the estimate from the scan
                m = re.search(r"Total reclaimed space:\s*(\S+)", r.stdout)
                res["freed"] += parse_size(m.group(1)) if m else it.size
        except Exception as e:
            msg = e.args[0] if e.args else str(e)
            if isinstance(msg, list):
                msg = msg[0]
            if isinstance(e, GLib.Error):
                msg = e.message
            res["errors"].append((it.title, str(msg)))
    if root:
        if progress:
            progress(len(user), total, "Waiting for your password…")
        r = run(["pkexec", "/bin/sh", "-c", root_script(root)], timeout=3600)
        if r.returncode in (126, 127) and "OK:" not in r.stdout and "FAIL:" not in r.stdout:
            res["errors"].append(("System cleanup", "The password dialog was cancelled"))
        else:
            for n, i in enumerate(root):
                if f"OK:{n}" in r.stdout:
                    res["freed"] += i.size
                else:
                    res["errors"].append((i.title, "The command failed"))
    if progress:
        progress(total, total, "Done")
    return res


ROOT_PROGRAMS = {"apt-get", "snap", "journalctl", "find", "env"}
SNAP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def root_script(items):
    """One shell script for all root items, so the password is asked once.

    Every command is an argv list from a fixed allowlist and is shell-quoted, so
    nothing found on disk can inject shell code into the root shell.
    """
    lines = []
    for n, it in enumerate(items):
        cmds = it.cmd
        if not cmds or not all(isinstance(c, list) and c and c[0] in ROOT_PROGRAMS for c in cmds):
            raise ValueError(f"Not an allowed system command: {it.title}")
        body = " && ".join(shlex.join(c) for c in cmds)
        lines.append(f"( {body} ) >/dev/null 2>&1 && echo OK:{n} || echo FAIL:{n}")
    return "\n".join(lines)


def delete_from_trash(originals):
    """Permanently delete the Trash entries that came from `originals`. Returns freed bytes."""
    items = trash_items(find_trashed(originals))
    return clean_items(items)["freed"]


# ---------------------------------------------------------------- folder explorer
class FolderSizes:
    """Sizes of every folder ≥ 5 MB in home, from one `du` pass; smaller ones are measured on demand."""

    def __init__(self, root=HOME):
        self.root, self.sizes, self.ready = root, {}, False

    def scan(self):
        try:
            low = ["nice", "-n", "10"] + (["ionice", "-c3"] if shutil.which("ionice") else [])
            r = subprocess.run(low + ["du", "-x", "-B1", "-0", "-t", "5M", self.root], capture_output=True, timeout=3600)
        except (OSError, subprocess.TimeoutExpired):
            r = None
        sizes = {}
        if r:
            for rec in r.stdout.split(b"\0"):
                if rec:
                    sz, _, p = rec.partition(b"\t")
                    try:
                        sizes[os.fsdecode(p)] = int(sz)
                    except ValueError:
                        pass
        self.sizes, self.ready = sizes, True
        return sizes

    def children(self, d):
        """[(name, path, is_dir, size)] biggest first."""
        out, small = [], []
        try:
            ents = list(os.scandir(d))
        except OSError:
            return []
        for e in ents:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                if e.path in self.sizes:
                    out.append((e.name, e.path, True, self.sizes[e.path]))
                else:
                    small.append(e)
            else:
                out.append((e.name, e.path, False, file_size(st)))
        got = du_sizes([e.path for e in small])
        out += [(e.name, e.path, True, got.get(e.path, 0)) for e in small]
        out.sort(key=lambda x: -x[3])
        return out

    def size(self, p):
        if p not in self.sizes:
            self.sizes[p] = du_one(p)
        return self.sizes[p]

    def forget(self, p, size):
        """After removing p: drop it and subtract its size from every parent."""
        self.sizes.pop(p, None)
        d = os.path.dirname(p)
        while d.startswith(self.root):
            if d in self.sizes:
                self.sizes[d] = max(0, self.sizes[d] - size)
            if d == self.root:
                break
            d = os.path.dirname(d)


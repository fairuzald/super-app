# PyInstaller spec for the Windows (.exe folder) and macOS (.app) builds.
#   Windows: packaging/windows/build.sh (MSYS2 UCRT64)      macOS: packaging/macos/build.sh (Homebrew)
# Python + GTK + libadwaita are bundled. Plugins ship as plain folders inside the app and their tools run
# through the app itself (`super-app --run-script`), so the imports of every plugin tool are bundled too.
import ast
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
CORE, HUB, PLUGINS = ROOT / "packages" / "core" / "src", ROOT / "packages" / "hub" / "src", ROOT / "plugins"
sys.path[:0] = [str(CORE), str(HUB)]          # so collect_submodules can see our packages
MAC = sys.platform == "darwin"


def plugin_files():
    for plugin in sorted(PLUGINS.iterdir()):
        if (plugin / "plugin.toml").is_file():
            for dirpath, dirnames, files in os.walk(plugin):
                dirnames[:] = [d for d in dirnames if d not in ("tests", "linux", "__pycache__")]
                for f in files:
                    yield plugin, Path(dirpath) / f


def plugin_imports():
    """Every module the plugin tools import (except their own sibling files), incl. gi.repository.X."""
    mods, local = set(), {p.stem for _pl, p in plugin_files() if p.suffix == ".py"}
    for _pl, p in plugin_files():
        if p.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module)
                if node.module == "gi.repository":
                    mods |= {f"gi.repository.{a.name}" for a in node.names}
    keep = set()
    for m in mods:
        top = m.split(".")[0]
        if top in local or top in ("superapp_core", "superapp_hub"):
            continue
        if top in ("fcntl", "termios") and not MAC:
            continue                                  # Linux-only, imported inside try/except
        if top == "winreg" and MAC:
            continue
        keep.add(m)
    return sorted(keep)


datas = [(str(HUB / "superapp_hub" / "assets"), "superapp_hub/assets")]
for plugin, f in plugin_files():
    rel = os.path.relpath(f.parent, plugin)          # MSYS2 mixes / and \ - relpath copes, relative_to doesn't
    datas.append((str(f), os.path.normpath(os.path.join("plugins", plugin.name, rel))))

# Helpers the tools call: gdbus (single-instance apps + hub actions on Windows), openssl (Wi-Fi link cert).
binaries = []
for tool in ("gdbus", "openssl") if not MAC else ("openssl",):
    exe = shutil.which(tool)
    if exe:
        binaries.append((exe, "."))

hidden = sorted(set(collect_submodules("superapp_core") + collect_submodules("superapp_hub") + [
    f"{pkg}.{f[:-3]}" for src, pkg in ((CORE, "superapp_core"), (HUB, "superapp_hub"))
    for f in os.listdir(src / pkg) if f.endswith(".py") and f != "__init__.py"] + plugin_imports() + ["segno"]))

a = Analysis(
    [str(ROOT / "packaging" / "pyinstaller" / "launch.py")],
    pathex=[str(CORE), str(HUB)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hidden,
    hooksconfig={"gi": {"icons": ["Adwaita"], "themes": ["Adwaita"], "languages": ["en_US"],
                        "module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Adw": "1", "GdkPixbuf": "2.0"}}},
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
icon = ROOT / "packaging" / ("macos/super-app.png" if MAC else "windows/super-app.ico")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="super-app", console=False, icon=str(icon))
coll = COLLECT(exe, a.binaries, a.datas, name="super-app")
if MAC:
    app = BUNDLE(coll, name="Super App.app", icon=str(icon), bundle_identifier="local.fairuz.SuperApp",
                 info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0",
                             "CFBundleShortVersionString": (ROOT / "VERSION").read_text().strip()})

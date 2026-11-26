# PyInstaller spec for the Windows build. Run from an MSYS2 UCRT64 shell: packaging/windows/build.sh
# Plugins ship as plain folders next to the exe (they're loaded from disk, like user add-ons).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
CORE, HUB = ROOT / "packages" / "core" / "src", ROOT / "packages" / "hub" / "src"
sys.path[:0] = [str(CORE), str(HUB)]          # so collect_submodules can see our packages

datas = [(str(HUB / "superapp_hub" / "assets"), "superapp_hub/assets")]
for plugin in sorted((ROOT / "plugins").iterdir()):
    if not (plugin / "plugin.toml").is_file():
        continue
    for dirpath, dirnames, files in os.walk(plugin):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "linux", "__pycache__")]
        rel = os.path.relpath(dirpath, plugin)        # MSYS2 mixes / and \ - relpath copes, relative_to doesn't
        dest = os.path.normpath(os.path.join("plugins", plugin.name, rel))
        datas += [(os.path.join(dirpath, f), dest) for f in files]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launch.py")],
    pathex=[str(CORE), str(HUB)],
    datas=datas,
    hiddenimports=sorted(set(collect_submodules("superapp_core") + collect_submodules("superapp_hub") + [
        f"{pkg}.{f[:-3]}" for src, pkg in ((CORE, "superapp_core"), (HUB, "superapp_hub"))
        for f in os.listdir(src / pkg) if f.endswith(".py") and f != "__init__.py"])),
    hooksconfig={"gi": {"icons": ["Adwaita"], "themes": ["Adwaita"], "languages": ["en_US"],
                        "module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Adw": "1"}}},
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="super-app", console=False,
          icon=str(ROOT / "packaging" / "windows" / "super-app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="super-app")

# PyInstaller spec for the Windows build. Run from an MSYS2 UCRT64 shell: packaging/windows/build.sh
# Plugins ship as plain folders next to the exe (they're loaded from disk, like user add-ons).
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
CORE, HUB = ROOT / "packages" / "core" / "src", ROOT / "packages" / "hub" / "src"

datas = [(str(HUB / "superapp_hub" / "assets"), "superapp_hub/assets")]
for plugin in sorted((ROOT / "plugins").iterdir()):
    if not (plugin / "plugin.toml").is_file():
        continue
    for f in plugin.rglob("*"):
        rel = f.relative_to(plugin)
        if f.is_file() and rel.parts[0] not in ("tests", "linux") and "__pycache__" not in rel.parts:
            datas.append((str(f), str(Path("plugins") / plugin.name / rel.parent)))

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launch.py")],
    pathex=[str(CORE), str(HUB)],
    datas=datas,
    hiddenimports=collect_submodules("superapp_core") + collect_submodules("superapp_hub"),
    hooksconfig={"gi": {"icons": ["Adwaita"], "themes": ["Adwaita"], "languages": ["en_US"],
                        "module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Adw": "1"}}},
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="super-app", console=False,
          icon=str(ROOT / "packaging" / "windows" / "super-app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="super-app")

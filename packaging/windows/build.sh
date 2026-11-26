#!/usr/bin/env bash
# Windows build. Run in an MSYS2 UCRT64 shell (https://www.msys2.org) after installing:
#   pacman -S --needed mingw-w64-ucrt-x86_64-{gtk4,libadwaita,python,python-gobject,librsvg,adwaita-icon-theme,pyinstaller,openssl,python-pip,python-pytest}
#   python -m pip install --break-system-packages segno
# Output: dist/windows/super-app/super-app.exe  (then installer.iss makes the setup .exe)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python -m PyInstaller --noconfirm --clean --distpath dist/windows --workpath build/windows packaging/pyinstaller/super-app.spec
./dist/windows/super-app/super-app.exe --check
echo "Built dist/windows/super-app"

#!/usr/bin/env bash
# macOS build: dist/Super-App-<version>.dmg with "Super App.app" (Python + GTK bundled).
#   brew install gtk4 libadwaita pygobject3 librsvg adwaita-icon-theme openssl
#   $(brew --prefix)/bin/python3 -m pip install --break-system-packages pyinstaller segno pillow pytest
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$(brew --prefix)/bin/python3}"
VERSION="${VERSION:-$(cat VERSION)}"; VERSION="${VERSION#v}"
"$PY" -m PyInstaller --noconfirm --clean --distpath dist/macos --workpath build/macos packaging/pyinstaller/super-app.spec
"dist/macos/Super App.app/Contents/MacOS/super-app" --check
rm -f "dist/Super-App-$VERSION.dmg"
hdiutil create -volname "Super App" -srcfolder "dist/macos/Super App.app" -ov -format UDZO "dist/Super-App-$VERSION.dmg"
echo "Built dist/Super-App-$VERSION.dmg"

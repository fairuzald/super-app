#!/usr/bin/env bash
# Install Super App for this user, running from this checkout (no root needed).
#   ./packaging/linux/install.sh
# For a system-wide package, build the .deb instead: ./packaging/linux/build-deb.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPS=~/.local/share/applications ICONS=~/.local/share/icons/hicolor/scalable/apps

missing=()
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null \
  || missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)
if ((${#missing[@]})); then
  echo "Needs: ${missing[*]}  →  sudo apt install ${missing[*]}" >&2
  exit 1
fi

chmod +x "$ROOT/bin/super-app"
mkdir -p "$APPS" "$ICONS"
cp "$ROOT/packages/hub/src/superapp_hub/assets/local.fairuz.SuperApp.svg" "$ICONS/"
sed "s#@EXEC@#/usr/bin/python3 \"$ROOT/bin/super-app\"#" "$ROOT/packaging/linux/super-app.desktop.in" \
  > "$APPS/local.fairuz.SuperApp.desktop"

for inst in "$ROOT"/plugins/*/install.sh; do bash "$inst"; done

gtk-update-icon-cache -q -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true
/usr/bin/python3 "$ROOT/bin/super-app" --check
echo "Installed. Open “Super App” from the app menu."

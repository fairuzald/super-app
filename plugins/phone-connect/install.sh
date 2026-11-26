#!/usr/bin/env bash
# Linux user install of the Phone Connect app (called by packaging/linux/install.sh).
# Hidden from the app menu: Super App opens it. The entry still gives its window the right icon.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS=~/.local/share/applications ICONS=~/.local/share/icons/hicolor/scalable/apps
chmod +x "$DIR/app/phone-connect.py" "$DIR/app/phone-mirror.sh"
mkdir -p "$APPS" "$ICONS" ~/.config/systemd/user
cp "$DIR/app/assets/phone-connect.svg" "$ICONS/local.fairuz.PhoneConnect.svg"
sed "s#@PLUGIN@#$DIR#g" "$DIR/linux/local.fairuz.PhoneConnect.desktop.in" > "$APPS/local.fairuz.PhoneConnect.desktop"
# Display keeper: laptop at 100% scale, every screen at max refresh, layouts remembered.
sed "s#@PLUGIN@#$DIR#g" "$DIR/linux/phone-connect-displays.service.in" > ~/.config/systemd/user/phone-connect-displays.service
if command -v systemctl >/dev/null; then
  systemctl --user daemon-reload || true
  systemctl --user enable --now phone-connect-displays.service 2>/dev/null || true
  systemctl --user restart phone-connect-displays.service 2>/dev/null || true
fi
echo "Phone Connect installed."

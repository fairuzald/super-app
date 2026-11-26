#!/usr/bin/env bash
# Linux user install of the Storage Cleaner app (called by packaging/linux/install.sh).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS=~/.local/share/applications ICONS=~/.local/share/icons/hicolor/scalable/apps
chmod +x "$DIR/app/storage-cleaner.py"
mkdir -p "$APPS" "$ICONS"
cp "$DIR/app/assets/local.fairuz.StorageCleaner.svg" "$ICONS/"
sed "s#@PLUGIN@#$DIR#g" "$DIR/linux/local.fairuz.StorageCleaner.desktop.in" > "$APPS/local.fairuz.StorageCleaner.desktop"
echo "Storage Cleaner installed."

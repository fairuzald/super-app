#!/usr/bin/env bash
# Build dist/super-app_<version>_all.deb (Debian/Ubuntu). Installs to /usr/lib/super-app.
#   ./packaging/linux/build-deb.sh            then   sudo apt install ./dist/super-app_*_all.deb
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-$(cat "$ROOT/VERSION")}"
VERSION="${VERSION#v}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
LIB=/usr/lib/super-app
D="$STAGE$LIB"

mkdir -p "$D/lib" "$D/plugins" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/lib/systemd/user" "$STAGE/DEBIAN"

cp -r "$ROOT/packages/core/src/superapp_core" "$ROOT/packages/hub/src/superapp_hub" "$D/lib/"
for p in "$ROOT"/plugins/*/; do
  id="$(basename "$p")"
  mkdir -p "$D/plugins/$id"
  cp "$p/plugin.toml" "$p/plugin.py" "$D/plugins/$id/"
  [ -d "$p/app" ] && cp -r "$p/app" "$D/plugins/$id/"
done
find "$D" -name __pycache__ -prune -exec rm -rf {} +

cat > "$STAGE/usr/bin/super-app" <<EOF
#!/usr/bin/python3
import sys
sys.path.insert(0, "$LIB/lib")
from superapp_hub.app import main
sys.exit(main())
EOF
chmod 755 "$STAGE/usr/bin/super-app"

sed "s#@EXEC@#super-app#" "$ROOT/packaging/linux/super-app.desktop.in" \
  > "$STAGE/usr/share/applications/local.fairuz.SuperApp.desktop"
cp "$ROOT/packages/hub/src/superapp_hub/assets/local.fairuz.SuperApp.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
for p in "$ROOT"/plugins/*/; do
  id="$(basename "$p")"
  for tpl in "$p"linux/*.desktop.in; do
    [ -e "$tpl" ] || continue
    sed "s#@PLUGIN@#$LIB/plugins/$id#g" "$tpl" > "$STAGE/usr/share/applications/$(basename "${tpl%.in}")"
  done
  for tpl in "$p"linux/*.service.in; do
    [ -e "$tpl" ] || continue
    sed "s#@PLUGIN@#$LIB/plugins/$id#g" "$tpl" > "$STAGE/usr/lib/systemd/user/$(basename "${tpl%.in}")"
  done
  find "$p/app/assets" -maxdepth 1 -name 'local.fairuz.*.svg' -exec cp {} "$STAGE/usr/share/icons/hicolor/scalable/apps/" \; 2>/dev/null || true
done
[ -f "$ROOT/plugins/phone-connect/app/assets/phone-connect.svg" ] && \
  cp "$ROOT/plugins/phone-connect/app/assets/phone-connect.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/local.fairuz.PhoneConnect.svg"

SIZE="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: super-app
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, librsvg2-common
Recommends: adb, scrcpy, ffmpeg, qrencode
Suggests: v4l2loopback-dkms, gnome-remote-desktop
Maintainer: Super App contributors <noreply@github.com>
Description: All your desktop tools in one place, with plugins
 A GTK4 hub that shows a live card per plugin. Ships with Phone Connect
 (Android mirroring, webcam, second screen) and Storage Cleaner.
EOF
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/super-app_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"

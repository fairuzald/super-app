#!/usr/bin/env bash
# Mirror + control the Tecno phone on this laptop with scrcpy.
#   ./phone-mirror.sh              -> use any already-connected device (USB or Wi-Fi)
#   ./phone-mirror.sh IP:PORT      -> connect over Wi-Fi first (port from Wireless debugging screen)
#   ./phone-mirror.sh pair IP:PORT CODE  -> one-time pairing (from "Pair device with pairing code")
set -e
ADB="$(command -v adb || echo "$HOME/Android/Sdk/platform-tools/adb")"
export ADB  # scrcpy uses the same adb

if [ "$1" = "pair" ]; then
  "$ADB" pair "$2" "$3"; exit
fi
if [ -n "$1" ]; then
  "$ADB" connect "$1"
fi
"$ADB" devices
exec scrcpy --stay-awake --turn-screen-off --max-size=1600 "${@:2}"

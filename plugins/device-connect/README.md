# Device Connect

Use an Android phone or tablet from your computer over Wi-Fi. A Super App plugin.

- **Linux, Windows and macOS:**
  - pairing (QR, code or USB) and connecting over Wi-Fi;
  - mirroring and controlling the phone, opening apps in their own windows;
  - screenshots and screen recording;
  - sending and browsing files;
  - the tablet Control panel and Live monitor, and the Wi-Fi link.
- **Linux (GNOME) only:**
  - phone as webcam, microphone or speaker;
  - extra display and drawing pad;
  - notifications and SMS through GSConnect;
  - the display keeper.

These need PipeWire, v4l2loopback, GNOME Remote Desktop and uinput.

| Feature | How |
|---|---|
| **Show phone screen** | scrcpy, mouse + keyboard control |
| **Shared clipboard** | automatic while the screen is shown |
| **Phone as webcam** | scrcpy camera → v4l2loopback “Phone Camera” |
| **Send files / Browse phone files** | adb push → phone Download; browse any folder, tick files, copy anywhere |
| **Open an app** | scrcpy virtual display, one app per window |
| **Notifications & SMS** | GSConnect + KDE Connect app |
| **Screenshot / Record screen** | ~/Pictures/Phone, ~/Videos/Phone |
| **Phone buttons** | Unlock, Lock, Back, Home, Recents, volume |
| **Tablet: Control panel** | volume, mic mute, media, brightness, night light, dark mode, DND, screenshot, lock, app launchers |
| **Tablet: Live monitor** | clock, CPU, RAM, disk, battery, network, Docker, busiest apps, phone battery |
| **Tablet: Drawing pad** | pen/touch → virtual pen tablet on the laptop (pressure, tilt, eraser) |

### Camera & audio
- **Phone as webcam**: Android 12+ uses scrcpy's camera. Older devices (e.g. Android 10 tablets) stream the camera from
  the device's browser over the adb tunnel into the same "Phone Camera" device (ffmpeg → v4l2loopback). Keep the page open.
- **Use as microphone**: the device mic (browser capture) appears as a laptop input named "<device> mic"
  (PipeWire virtual source). Works on any Android version.
- **Use as speaker**: laptop sound plays on the device (browser playback, ~100 ms delay). Creates a virtual output
  "<device> speaker", makes it the default while on, and gives the laptop its own output back when turned off.
- Sound *from* the device can't be captured before Android 11.

### Second screen (tablet)
- **Extra display**: the tablet becomes a real extra monitor (Settings › Displays). Uses GNOME Remote Desktop in "extend" mode + Microsoft Remote Desktop on the tablet, tunnelled over adb to `localhost:3389`. RDP is on only while in use; login details are shown in the app (stored in `~/.local/share/phone-connect/rdp`, private).
- Pair the tablet like a phone, then pick it under **Second screen → Show on**. Control panel, Live monitor and Drawing pad open there. Pick your phone to use the phone instead.
- Pages are served by `companion.py` on 127.0.0.1 only and reach the device through `adb reverse` (token-protected).
- Launcher buttons: edit `~/.config/phone-connect-panel.json`.
- Drawing pad needs a one-time udev rule (`/etc/udev/rules.d/60-phone-connect-uinput.rules`, asked with your password) so the app can create a virtual pen.

## Use
- Open **Device Connect** from the app menu. Phone: Developer options → **Wireless debugging** on, same Wi-Fi.
- First time: **Pair a phone** → pick **QR code**, **Pairing code**, or **USB cable**.
- After that it finds and connects the phone by itself (mDNS via avahi). Several phones? ☰ → **Your phones** to switch or remove.
- ☰ → **Settings**: phone screen on/off while mirroring, keep screen on, sound output, quality, window, control.
- Uses the Android SDK adb (`~/Android/Sdk/platform-tools/adb`) when present — the distro adb drops Wi-Fi connections.

## Files
Plugin: `plugin.toml` (manifest) and `plugin.py` (the hub card). The app itself is in `app/`:
- `companion.py` – tablet server (control panel, monitor, drawing pad); page in `assets/companion.html`.
- `device-connect.py` – the app (Python, GTK4 + libadwaita, system `/usr/bin/python3`).
- `../install.sh` – adds the (hidden) app entry, icon and display-keeper service. Run by `packaging/linux/install.sh`; re-run if you move the folder.
- `assets/` – app icon and illustration (SVG).
- `phone-mirror.sh` – terminal fallback: `./phone-mirror.sh IP:PORT`.
- Settings: `~/.config/phone-connect.json`.
- Closing the app turns everything off: mirror/app windows, webcam, recording (saved first), the phone's screen timeout is restored, the phone is disconnected and the adb service stopped.

## Needs
`adb`, `scrcpy`, `qrencode`, `avahi-daemon` (the app offers to install scrcpy; webcam and GSConnect set themselves up on first use).

### Wi-Fi link (no cable, e.g. Android 10 tablets)
Second screen → **Wi-Fi link**: serves the tablet page over HTTPS on the local network (`https://<laptop-ip>:47801`,
self-signed, secret token in `~/.config/phone-connect-token`). The firewall is opened for the local subnet only
(ports 47801 and 3389) with one password prompt. Scan the QR on the tablet, accept the certificate once, add to home
screen. Camera & audio, Control panel, Monitor, Drawing pad and Extra display (RDP to the laptop IP) then work without
adb, so restarts of the tablet need no cable. Mirroring the tablet itself still needs debugging.

### Display keeper (`displays.py`)
Background service `phone-connect-displays` (systemd user unit, starts at login). Whenever screens change it:
keeps the laptop at 100 % scale, sets every screen to its highest refresh rate, keeps the laptop primary for new
screen combinations, and remembers your arrangement per combination (`~/.config/phone-connect-displays.json`).
Turn off: `systemctl --user disable --now phone-connect-displays`.

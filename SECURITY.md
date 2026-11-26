# Security

## Reporting a problem

Please report security issues privately through GitHub: **Security › Report a vulnerability** on this repository.
Don't open a public issue for them.

## How Super App protects you

**Plugins**
- Plugins are Python code that runs as you, like any app you install. Only add plugins you trust.
- Plugin folders that another user could change are refused: world-writable folders, or ones owned by another
  user (Linux and macOS). The Plugins dialog lists them under "Not loaded".
- A plugin's entry file and icon must be inside its own folder.
- An installed build only runs plugin tools from the bundled or user plugin folders (`--run-script`).

**Storage Cleaner**
- It only removes things inside your home folder, plus your own Trash / Recycle Bin.
- Your home folder itself, Documents, Downloads, app-settings folders, and key or credential folders are always
  refused: `.ssh`, `.gnupg`, keyrings, macOS Keychains, and Windows Credentials, Protect and Crypto.
- Links and Windows junctions are removed, never followed.
- System cleanup (Linux only) runs fixed commands from an allowlist through `pkexec`, shell-quoted, with one
  password prompt.

**Device Connect**
- Phone and tablet pages need a 192-bit random token on every API and WebSocket request. The token is checked in
  constant time and stored in a file only you can read.
- The page server listens on localhost only. The device reaches it through an `adb reverse` tunnel.
- The optional Wi-Fi link serves over HTTPS with a self-signed certificate:
  - minimum TLS 1.2;
  - bound to your Wi-Fi address only;
  - on Linux, firewall rules open it to your local subnet only.
- Responses send a strict Content-Security-Policy, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
  `no-store` caching.
- Request size and connection count are limited.
- The control panel only runs actions this computer supports, with validated arguments.
- Launcher buttons run commands from your own config file. The file is ignored if another user could edit it.
- Paths sent to the phone's shell are quoted.
- Wireless debugging uses Android's own pairing (QR code or pairing code). Nothing is sent to any server outside
  your network.

## Known limits

- The Wi-Fi link certificate is self-signed, so the tablet's browser shows a warning the first time.
- Anyone who has the page link (with its token) can use the control panel. Treat the link like a password.
  Delete `~/.config/phone-connect-token` to make a new one.

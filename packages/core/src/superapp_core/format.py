"""Human-friendly numbers for cards."""
from __future__ import annotations

import time


def size(n: float | int | None) -> str:
    """Decimal units, like GNOME Files and Windows Explorer's "GB"."""
    n = float(n or 0)
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def ago(t: float | None, now: float | None = None) -> str:
    s = (now or time.time()) - (t or 0)
    if s < 120:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"

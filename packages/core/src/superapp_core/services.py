"""Default, toolkit-free implementations of the ports."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence

from .platform import Platform


class ProcessLauncher:
    """Starts a detached process (it outlives the hub)."""

    def launch(self, argv: Sequence[str]) -> bool:
        kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            subprocess.Popen(list(argv), **kw)
            return True
        except OSError:
            return False


class NullBridge:
    """For platforms without an app bus: tools are never "running", actions are dropped."""

    def is_running(self, app_id: str) -> bool:
        return False

    def activate(self, app_id: str, action: str) -> None:
        return None


def find_python(platform: Platform) -> str:
    """The interpreter for script tools. Frozen builds have no usable sys.executable, so look on PATH."""
    if platform is Platform.LINUX and os.access("/usr/bin/python3", os.X_OK):
        return "/usr/bin/python3"            # the system Python has the GTK bindings
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or "python3"


def open_in_file_manager(path: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False

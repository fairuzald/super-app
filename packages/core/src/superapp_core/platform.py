"""Which operating system we run on, as a small value type plugins can declare support for."""
from __future__ import annotations

import sys
from enum import StrEnum


class Platform(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"

    @classmethod
    def current(cls) -> Platform:
        if sys.platform.startswith("win"):
            return cls.WINDOWS
        if sys.platform == "darwin":
            return cls.MACOS
        return cls.LINUX

    @property
    def label(self) -> str:
        return {"linux": "Linux", "windows": "Windows", "macos": "macOS"}[self.value]

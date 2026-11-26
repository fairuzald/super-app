"""What a plugin shows on its hub card. Plain immutable values - any UI can render them."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class Action:
    """A button. `id=None` means "just open the tool"."""
    label: str
    icon: str
    id: str | None = None


class MeterKind(StrEnum):
    BAR = "bar"
    RING = "ring"


@dataclass(frozen=True)
class Meter:
    kind: MeterKind
    value: float                 # 0..1
    warn: bool = False
    tooltip: str = ""

    def __post_init__(self):
        object.__setattr__(self, "value", min(1.0, max(0.0, float(self.value))))


@dataclass(frozen=True)
class ToolStatus:
    """One card: a subtitle, a big headline number/word, a caption under it, an optional meter, buttons."""
    subtitle: str
    headline: str
    caption: str
    meter: Meter | None = None
    actions: tuple[Action, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QuickAction:
    """A chip in the hub's "Quick actions" row. Lower priority numbers come first."""
    action: Action
    priority: int = 50

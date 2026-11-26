"""A card-only plugin: no separate app, works on every OS. Copy this to start your own add-on."""
import shutil
from pathlib import Path

from superapp_core import Action, Meter, MeterKind, Plugin, QuickAction, ToolStatus
from superapp_core.format import size
from superapp_core.services import open_in_file_manager


class DiskSpacePlugin(Plugin):
    def drive(self) -> str:
        return Path.home().anchor or "/"          # C:\ on Windows, / elsewhere

    def status(self) -> ToolStatus:
        du = shutil.disk_usage(self.drive())
        used = du.used / du.total if du.total else 0
        return ToolStatus(
            subtitle=f"Drive {self.drive()}", headline=size(du.free), caption=f"free of {size(du.total)}",
            meter=Meter(MeterKind.RING, used, warn=used >= 0.9, tooltip="Drive used"),
            actions=(Action("Open home folder", "folder-symbolic", "home"),))

    def quick_actions(self, status: ToolStatus) -> list[QuickAction]:
        return [QuickAction(Action("Open home folder", "folder-symbolic", "home"), 60)]

    def open(self, action=None):
        if not open_in_file_manager(str(Path.home())):
            self.ctx.notifier.notify("Couldn’t open the file manager")

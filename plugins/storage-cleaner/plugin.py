"""Hub card for Storage Cleaner. The app itself lives in app/ and runs on its own."""
import shutil
from pathlib import Path

from superapp_core import Action, AppPlugin, Meter, MeterKind, QuickAction, ToolStatus
from superapp_core.format import ago, size
from superapp_core.paths import read_json


class StorageCleanerPlugin(AppPlugin):
    app_id = "local.fairuz.StorageCleaner"
    script = "app/storage-cleaner.py"

    def _scan(self) -> dict:
        """Written by the app after each scan: time, freeable, trash."""
        return read_json(self.ctx.paths.cache / "storage-cleaner" / "last-scan.json")

    def status(self) -> ToolStatus:
        du = shutil.disk_usage(Path.home().anchor or "/")
        scan = self._scan()
        if scan.get("time"):
            headline, caption = size(scan.get("freeable")), f"safe to free · checked {ago(scan['time'])}"
        else:
            headline, caption = "Scan", "open it to see what you can free"
        used = du.used / du.total if du.total else 0
        return ToolStatus(
            subtitle=f"{size(du.free)} free of {size(du.total)}", headline=headline, caption=caption,
            meter=Meter(MeterKind.RING, used, warn=used >= 0.93, tooltip="Disk used"),
            actions=(Action("Clean up", "starred-symbolic", "clean-up"),
                     Action("Explore", "system-search-symbolic", "explore")))

    def quick_actions(self, status: ToolStatus) -> list[QuickAction]:
        scan = self._scan()
        free, trash = scan.get("freeable"), scan.get("trash")
        return [QuickAction(Action(f"Free up {size(free)}" if free else "Find space to free",
                                   "starred-symbolic", "clean-up"), 30),
                QuickAction(Action(f"Empty Trash ({size(trash)})" if trash else "Empty Trash",
                                   "user-trash-symbolic", "trash"), 31)]

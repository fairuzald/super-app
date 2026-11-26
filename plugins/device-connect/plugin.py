"""Hub card for Device Connect. The app itself lives in app/ and runs on its own."""
from superapp_core import Action, AppPlugin, Meter, MeterKind, QuickAction, ToolStatus
from superapp_core.paths import read_json

SHOW = Action("Show screen", "phone-symbolic", "show-screen")
SEND = Action("Send", "send-to-symbolic", "send-files")


class DeviceConnectPlugin(AppPlugin):
    app_id = "local.fairuz.PhoneConnect"
    script = "app/device-connect.py"

    def _state(self) -> dict:
        """Written by the app: model, battery, charging, how (Wi-Fi/USB), mirror."""
        return read_json(self.ctx.paths.cache / "phone-connect" / "status.json")

    def connected(self) -> bool:
        return self.is_running() and bool(self._state().get("model"))

    def status(self) -> ToolStatus:
        if not self.is_running():
            return ToolStatus("Not open", "Off", "open it to connect your phone",
                              actions=(Action("Open", "phone-symbolic"),))
        st = self._state()
        if not st.get("model"):
            return ToolStatus("No phone connected", "Waiting", "for your phone to connect",
                              actions=(Action("Connect", "phone-symbolic"),))
        b = st.get("battery")
        show = Action("Hide screen" if st.get("mirror") else "Show screen", "phone-symbolic", "show-screen")
        return ToolStatus(
            subtitle=f"{st['model']} · {st.get('how', 'Wi-Fi')}",
            headline=f"{b}%" if b is not None else "—",
            caption="battery, charging" if st.get("charging") else "battery",
            meter=Meter(MeterKind.BAR, (b or 0) / 100, warn=b is not None and b <= 20),
            actions=(show, SEND))

    def quick_actions(self, status: ToolStatus) -> list[QuickAction]:
        prio = 20 if status.meter else 80          # nothing to show yet: let other tools go first
        return [QuickAction(Action("Show phone screen", "phone-symbolic", "show-screen"), prio),
                QuickAction(Action("Send a file", "send-to-symbolic", "send-files"), prio + 1)]

    def open(self, action=None):
        if action == "show-screen" and not self.connected():
            self.ctx.notifier.notify("Connect your phone in Device Connect first")
            action = None
        super().open(action)

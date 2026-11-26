# Writing a plugin

A plugin is a folder. Put it in `plugins/` in this repo, or in the user add-ons folder
(Menu › Plugins › Plugins folder) and restart Super App.

```
my-plugin/
  plugin.toml
  plugin.py
  app/          # optional: your own app, if the plugin is more than a card
```

## plugin.toml

```toml
id = "my-plugin"                 # lowercase letters, digits, dashes; unique
name = "My Plugin"
version = "1.0.0"
description = "One line shown in the Plugins dialog."
entry = "plugin.py:MyPlugin"     # file inside this folder : class name
platforms = ["linux", "windows", "macos"]   # default: all
default_on = ["windows"]         # optional: where it starts switched on (default: all supported)
color = "#f59e0b"                # card glow + main button colour
icon = "app/assets/icon.svg"     # optional, relative to the folder
order = 50                       # card position, lower first
api = 1                          # plugin API version
```

## A card-only plugin

```python
from superapp_core import Action, Meter, MeterKind, Plugin, QuickAction, ToolStatus


class MyPlugin(Plugin):
    def status(self) -> ToolStatus:              # called every 3 s on the UI thread: keep it fast
        return ToolStatus(subtitle="Today", headline="42", caption="things done",
                          meter=Meter(MeterKind.BAR, 0.42),
                          actions=(Action("Do it", "starred-symbolic", "do-it"),))

    def quick_actions(self, status):
        return [QuickAction(Action("Do it now", "starred-symbolic", "do-it"), priority=40)]

    def open(self, action=None):
        self.ctx.notifier.notify(f"Pressed {action}")
```

`self.ctx` (`PluginContext`) gives you `platform`, `paths` (config/cache/data per OS), `launcher`, `bridge`,
`scheduler`, `notifier` and `script_runner`. Use those instead of importing GTK, so the plugin stays testable and portable.
Icons are freedesktop/Adwaita icon names.

## A plugin that is its own app

Subclass `AppPlugin`, set `app_id` and `script`, and implement `status()`:

```python
from superapp_core import AppPlugin, ToolStatus
from superapp_core.paths import read_json


class MyTool(AppPlugin):
    app_id = "org.example.MyTool"        # its GApplication id
    script = "app/my-tool.py"

    def status(self):
        st = read_json(self.ctx.paths.cache / "my-tool" / "status.json")   # written by your app
        return ToolStatus("Ready" if self.is_running() else "Not open", st.get("count", "—"), "items")
```

`open(action)` is inherited: it launches the app if needed, waits for it, then activates the GTK app action
`action` over D-Bus (Linux). Register those actions in your app with `Gio.SimpleAction`.

## Test it

```bash
SUPERAPP_PLUGINS=/path/to/folder-containing-my-plugin ./bin/super-app --check
```

A plugin that raises during import or `status()` is shown as an error on its card / in the Plugins dialog; it never
takes the hub down.

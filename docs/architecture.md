# Architecture

```
┌────────────── superapp_hub (GTK4) ──────────────┐        ┌──────── plugins/<id>/ ────────┐
│ app.py      composition root: picks services    │        │ plugin.toml   manifest         │
│ window.py   cards + quick actions               │ uses   │ plugin.py     Plugin subclass  │
│ widgets.py  GlowCard renders any ToolStatus     │◄──────►│ app/          optional own app │
│ plugins_dialog.py  on/off, errors, add-ons dir  │        └───────────────┬───────────────┘
│ gtk_services.py    DBusBridge, GLibScheduler,   │                        │ implements
│                    ToastNotifier                │                        ▼
└──────────────────────┬──────────────────────────┘        ┌──────── superapp_core ────────┐
                       │ depends on abstractions only       │ Plugin / AppPlugin            │
                       └───────────────────────────────────►│ ToolStatus, Action, Meter     │
                                                            │ ports: AppBridge, Launcher,   │
                                                            │        Scheduler, Notifier    │
                                                            │ PluginDiscovery → Factory →   │
                                                            │ PluginRegistry (+ Settings)   │
                                                            └───────────────────────────────┘
```

## Flow

1. `app.build_registry()` builds a `PluginContext` with concrete services for this OS
   (D-Bus bridge on Linux, a null bridge elsewhere) and a `PluginRegistry`.
2. `PluginDiscovery` scans folders in priority order - `$SUPERAPP_PLUGINS`, the user add-ons folder, then the
   bundled plugins - and parses each `plugin.toml` into a `PluginManifest`. Bad manifests become `LoadIssue`s.
3. For each plugin that supports this platform and is switched on (`settings.json`, default from `default_on`),
   `PluginFactory` imports its entry file under a unique module name and instantiates the class.
   Any exception is stored on the entry and shown in the Plugins dialog.
4. Every 3 s the window asks each plugin for `status()` and `quick_actions()`; `GlowCard` renders the result.
   Buttons call `plugin.open(action_id)`.
5. `AppPlugin.open()` starts the tool if needed, waits until it's on the bus, then sends the action
   (`org.gtk.Actions.Activate` on Linux). Tools publish live numbers in small JSON status files in the cache dir.

## SOLID in this codebase

- **Single responsibility** - discovery, instantiation, enable/disable state and persistence are separate classes
  (`PluginDiscovery`, `PluginFactory`, `PluginRegistry`, `Settings`). `GlowCard` only renders; plugins only decide
  what to show.
- **Open/closed** - adding a tool means adding a folder. The hub has no plugin-specific code: card accents are
  generated from each manifest's `color`, quick actions are collected and sorted by priority.
- **Liskov substitution** - the hub treats every `Plugin` the same; `AppPlugin` and card-only plugins
  (`disk-space`) are interchangeable. The tests run all bundled plugins through the same contract.
- **Interface segregation** - four tiny ports (`AppBridge`, `Launcher`, `Scheduler`, `Notifier`) instead of one
  "host" object; a card-only plugin never touches the bridge or launcher.
- **Dependency inversion** - plugins and `superapp_core` depend on the ports, never on GTK. Concrete services are
  chosen in one place (`app.py`), and the tests swap in fakes.

## Cross-platform

`superapp_core` is pure Python (3.11+, `tomllib`) and is tested on Linux, Windows and macOS.

**Packaging**
- The hub uses GTK4 + libadwaita. Windows builds get it from MSYS2, macOS builds from Homebrew.
- One PyInstaller spec bundles Python, GTK and the plugins:
  - Windows: an Inno Setup installer;
  - macOS: a `.app` inside a `.dmg`.
- In those builds, plugin tools run through the app itself (`super-app --run-script tool.py`, chosen by
  `services.script_runner`), so every tool shares the bundled GTK.

**Where OS differences live**
- Behind ports: `Paths.for_platform`, `ProcessLauncher`, `script_runner`, and `DBusBridge`, which reports
  "not running" where there is no session bus.
- Inside each plugin:
  - Device Connect keeps them in `osdeps.py` (adb lookup, mDNS via adb when there's no avahi, a feature table)
    and `laptop.py` (Windows/macOS stats and controls).
  - Storage Cleaner keeps them in `cleaner_core.py` (cache folders, Trash / Recycle Bin, Python fallbacks for
    `du`/`find`).
- Features with no equivalent are hidden, not shown broken.

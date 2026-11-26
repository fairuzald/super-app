# Storage Cleaner

A GNOME app (GTK4 + libadwaita) that shows what fills your disk and frees space safely.

## What it cleans

| Category | What it finds | Default |
|---|---|---|
| App caches | `~/.cache` (pip, yarn, pnpm, uv, thumbnails, browser caches, …) | Safe ones ticked; AI models, test browsers and unknown caches left for you |
| Developer caches | npm `_cacache`/`_npx`, Gradle, Cargo, Maven, VS Code/Cursor caches, old VS Code extension versions, old nvm Node versions, Android emulator images, Go modules | Caches ticked; old versions are not |
| Trash | Everything in your Trash, with where it came from | Ticked, removed for good |
| node_modules | Every `node_modules` under `~/Documents` (and `~/projects`, `~/code`, …) with the project's last activity | Ticked only for projects not touched in 30+ days |
| Python & build leftovers | `.venv`, `__pycache__`, `.pytest_cache`, `dist`/`build`, `.next`, `.turbo`, Rust `target`, … | Caches always ticked; venvs and builds only in inactive projects |
| Docker | Untagged images, stopped containers, build cache, unused images, unused volumes | First three ticked; volumes never (extra warning) |
| Old downloads | Unfinished downloads, duplicates, installers already installed, zips already unzipped, old installers/archives/pictures/videos/documents (with thumbnails) | Junk ticked; your pictures and documents are not |
| Large files | Single files over 500 MB (adjustable) | Nothing ticked |
| System cleanup | apt cache, old snap versions, journal logs over 200 MB, crash reports, unused packages | Asks for your password once (pkexec) |
| Explore folders | Drill-down of your home folder, biggest first, with a bin button | Top folders and key folders are protected |

“Review & clean up” on the home page gathers every ticked, safe item in one list.

## Safety

- Nothing is removed until you press **Clean up** and confirm a preview of what goes.
- Your own files (downloads, large files, explorer) only go to the **Trash**. Rebuildable things (caches, `node_modules`, build output) let you pick Trash or delete now.
- After moving to Trash you can delete *just those items* from the Trash. The rest of your Trash is left alone.
- Only paths inside your home folder can be removed. Your home folder itself, `Documents`, `Downloads`, `.config`, `.local`, the Trash folders, and key/credential folders (`.ssh`, `.gnupg`, `.aws`, `.kube`, keyrings, …) are always refused (`is_safe()` in `cleaner_core.py`).
- Root work goes through `pkexec`, so you type your password in the system dialog. Root commands are fixed argument lists from an allowlist (`apt-get`, `snap`, `journalctl`, `find`, `env`), shell-quoted, and snap names are validated first (`root_script()`).
- Symlinks are removed, never followed.

## Install

Part of Super App: run `packaging/linux/install.sh` from the repo root (or install the `.deb`).
`plugins/storage-cleaner/install.sh` on its own adds just this app's (hidden) entry (`~/.local/share/applications`) and its icon to the hicolor theme. It runs from this folder with the system Python (`/usr/bin/python3`, PyGObject, GTK 4, libadwaita). Nothing is installed with pip.

## Code layout

- `plugin.toml` + `plugin.py`: the Super App plugin (the hub card). The app is in `app/`:
- `cleaner_core.py`: all scanning and cleaning, with no GTK. Each category is a `Category` subclass with a `scan()` that returns `Item`s. To add a category, write a subclass and add it to `CATEGORIES`.
- `storage-cleaner.py`: the UI. It has the home page with tiles, `CleanPage` (one category, or the recommended list), and `ExplorerPage` (folder drill-down). Scans run in a thread pool and results reach the UI through `GLib.idle_add`.
- `assets/local.fairuz.StorageCleaner.svg`: the app icon.
- `tests/e2e.py`: end-to-end test.

## Tests

```bash
timeout -k 10 300 /usr/bin/python3 plugins/storage-cleaner/tests/e2e.py
```

The test builds a fake home folder at `~/sc-e2e-sandbox` with dummy caches, projects, downloads and Trash. It points the app at that folder (`SC_HOME`, `XDG_DATA_HOME`) and opens the real window. Then it cleans through the same buttons and dialogs you use, checks the files on disk, and saves screenshots to `tests/shots/`. Your real files and Trash are never touched; the last check confirms that. Results go to `tests/last-results.json`.

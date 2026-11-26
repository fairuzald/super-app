from pathlib import Path

import pytest

from superapp_core import Platform, PluginContext
from superapp_core.paths import Paths


class FakeBridge:
    def __init__(self):
        self.running: set[str] = set()
        self.activated: list[tuple[str, str]] = []

    def is_running(self, app_id):
        return app_id in self.running

    def activate(self, app_id, action):
        self.activated.append((app_id, action))


class FakeLauncher:
    def __init__(self, ok=True, starts=None):
        self.ok, self.calls, self.starts = ok, [], starts

    def launch(self, argv):
        self.calls.append(list(argv))
        if self.ok and self.starts:
            self.starts()
        return self.ok


class FakeScheduler:
    """Runs scheduled callbacks immediately, repeating while they return True (bounded)."""

    def call_later(self, seconds, fn):
        for _ in range(100):
            if not fn():
                return


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def notify(self, message):
        self.messages.append(message)


@pytest.fixture
def ctx(tmp_path):
    paths = Paths(config=tmp_path / "config", cache=tmp_path / "cache", data=tmp_path / "data")
    return PluginContext(platform=Platform.LINUX, paths=paths, bridge=FakeBridge(), launcher=FakeLauncher(),
                         scheduler=FakeScheduler(), notifier=FakeNotifier(), script_runner=("python3",))


def make_plugin(base: Path, pid: str, body: str = "", **manifest) -> Path:
    folder = base / pid
    folder.mkdir(parents=True)
    fields = {"id": pid, "name": pid.title(), "entry": "plugin.py:P", **manifest}
    lines = []
    for k, v in fields.items():
        lines.append(f"{k} = {v!r}".replace("'", '"') if not isinstance(v, list) else
                     f"{k} = [{', '.join(repr(x).replace(chr(39), chr(34)) for x in v)}]")
    (folder / "plugin.toml").write_text("\n".join(lines))
    (folder / "plugin.py").write_text(body or (
        "from superapp_core import Plugin, ToolStatus\n"
        "class P(Plugin):\n"
        "    def status(self):\n"
        "        return ToolStatus('sub', 'head', 'cap')\n"))
    return folder

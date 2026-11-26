from pathlib import Path

import pytest

from superapp_core import Meter, MeterKind, Platform
from superapp_core.format import ago, size
from superapp_core.paths import Paths, read_json, write_json


def test_windows_paths_use_appdata():
    p = Paths.for_platform(Platform.WINDOWS, env={"APPDATA": r"C:\R", "LOCALAPPDATA": r"C:\L"}, home=Path("/h"))
    assert p.config == Path(r"C:\R") and p.data == Path(r"C:\L")
    assert p.user_plugins == Path(r"C:\L") / "super-app" / "plugins"


def test_linux_paths_follow_xdg():
    p = Paths.for_platform(Platform.LINUX, env={"XDG_CONFIG_HOME": "/c"}, home=Path("/h"))
    assert p.config == Path("/c") and p.cache == Path("/h/.cache")
    assert p.settings_file == Path("/c/super-app/settings.json")


def test_json_roundtrip_and_bad_files(tmp_path):
    f = tmp_path / "a" / "b.json"
    write_json(f, {"x": 1})
    assert read_json(f) == {"x": 1}
    f.write_text("[1, 2]")
    assert read_json(f) == {}
    assert read_json(tmp_path / "missing.json") == {}


def test_meter_is_clamped():
    assert Meter(MeterKind.BAR, 1.7).value == 1.0
    assert Meter(MeterKind.RING, -1).value == 0.0


@pytest.mark.parametrize("n,out", [(0, "0 bytes"), (999, "999 bytes"), (39_000_000_000, "39.0 GB"),
                                   (7_100_000, "7.1 MB")])
def test_size(n, out):
    assert size(n) == out


def test_ago():
    assert ago(1000, now=1030) == "just now"
    assert ago(0, now=7200) == "2 h ago"


def test_current_platform_is_known():
    assert Platform.current() in set(Platform)

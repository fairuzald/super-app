from pathlib import Path

import pytest

from superapp_core import ManifestError, Platform, PluginManifest

ROOT = Path("/tmp/x")


def m(**kw):
    return PluginManifest.from_dict({"id": "demo", "name": "Demo", "entry": "plugin.py:P", **kw}, ROOT)


def test_defaults_support_every_platform():
    man = m()
    assert man.platforms == frozenset(Platform)
    assert man.default_on == man.platforms
    assert man.color == "#22d3ee"


def test_platforms_and_default_on():
    man = m(platforms=["linux", "windows"], default_on=["windows"])
    assert man.supports(Platform.LINUX) and not man.supports(Platform.MACOS)
    assert man.default_on == {Platform.WINDOWS}


@pytest.mark.parametrize("bad", [
    {"id": "Bad Id"}, {"entry": "plugin.py"}, {"entry": "../x.py:P"}, {"color": "red"},
    {"platforms": ["beos"]}, {"api": 2}, {"name": ""},
])
def test_rejects_bad_manifests(bad):
    with pytest.raises(ManifestError):
        m(**bad)


def test_icon_path_is_relative_to_root():
    assert m(icon="a/b.svg").icon_path == ROOT / "a" / "b.svg"
    assert m().icon_path is None

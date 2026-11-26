"""The plugins shipped in this repo must load against the core API on any OS (their apps may not)."""
from pathlib import Path

from superapp_core import Platform
from superapp_core.loader import PluginDiscovery, PluginFactory

PLUGINS = Path(__file__).resolve().parents[3] / "plugins"


def test_every_bundled_manifest_is_valid():
    manifests, issues = PluginDiscovery([PLUGINS]).find()
    assert issues == []
    assert {m.id for m in manifests} >= {"phone-connect", "storage-cleaner", "disk-space"}
    for m in manifests:
        assert m.icon_path is None or m.icon_path.exists(), m.id


def test_every_bundled_plugin_builds_and_reports(ctx):
    manifests, _ = PluginDiscovery([PLUGINS]).find()
    for m in manifests:
        plugin = PluginFactory(ctx).create(m)
        st = plugin.status()
        assert st.headline and st.subtitle, m.id
        plugin.quick_actions(st)


def test_phone_connect_closed_shows_open_button(ctx):
    m = next(m for m in PluginDiscovery([PLUGINS]).find()[0] if m.id == "phone-connect")
    st = PluginFactory(ctx).create(m).status()
    assert st.headline == "Off" and st.actions[0].id is None


def test_disk_space_runs_everywhere():
    m = next(m for m in PluginDiscovery([PLUGINS]).find()[0] if m.id == "disk-space")
    assert m.platforms == frozenset(Platform)
    assert Platform.LINUX not in m.default_on

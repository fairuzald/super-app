from conftest import make_plugin

from superapp_core import Platform
from superapp_core.loader import PluginDiscovery, PluginFactory
from superapp_core.registry import PluginRegistry
from superapp_core.settings import Settings


def registry(ctx, dirs, platform=Platform.LINUX):
    r = PluginRegistry(PluginDiscovery(dirs), PluginFactory(ctx), Settings(ctx.paths.settings_file), platform)
    r.load()
    return r


def test_loads_and_orders_plugins(ctx, tmp_path):
    make_plugin(tmp_path, "zeta", order=1)
    make_plugin(tmp_path, "alpha", order=5)
    r = registry(ctx, [tmp_path])
    assert [p.id for p in r.active()] == ["zeta", "alpha"]
    assert r.active()[0].status().headline == "head"


def test_unsupported_platform_is_listed_but_off(ctx, tmp_path):
    make_plugin(tmp_path, "winonly", platforms=["windows"])
    r = registry(ctx, [tmp_path])
    e = r.get("winonly")
    assert not e.supported and not e.enabled and e.plugin is None
    assert r.active() == []


def test_default_on_and_toggle_is_remembered(ctx, tmp_path):
    make_plugin(tmp_path, "opt", default_on=["windows"])
    r = registry(ctx, [tmp_path])
    assert not r.get("opt").enabled
    r.set_enabled("opt", True)
    assert [p.id for p in r.active()] == ["opt"]
    assert registry(ctx, [tmp_path]).get("opt").enabled        # persisted in settings.json


def test_broken_plugin_does_not_break_others(ctx, tmp_path):
    make_plugin(tmp_path, "good")
    make_plugin(tmp_path, "crash", body="raise RuntimeError('boom')\n")
    make_plugin(tmp_path, "notaplugin", body="class P: pass\n")
    (tmp_path / "badtoml").mkdir()
    (tmp_path / "badtoml" / "plugin.toml").write_text("id = = 1")
    r = registry(ctx, [tmp_path])
    assert [p.id for p in r.active()] == ["good"]
    assert "boom" in r.get("crash").error
    assert "subclass" in r.get("notaplugin").error
    assert any("badtoml" in i.where for i in r.issues)


def test_first_folder_wins_on_duplicate_ids(ctx, tmp_path):
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    make_plugin(user, "dup", version="2.0.0")
    make_plugin(bundled, "dup", version="1.0.0")
    r = registry(ctx, [user, bundled])
    assert r.get("dup").manifest.version == "2.0.0"
    assert len(r.issues) == 1


def test_world_writable_plugin_is_refused(ctx, tmp_path):
    import os
    if os.name != "posix":
        return
    folder = make_plugin(tmp_path, "shady")
    folder.chmod(0o777)
    r = registry(ctx, [tmp_path])
    assert r.get("shady") is None
    assert any("writable by everyone" in i.message for i in r.issues)


def test_icon_outside_the_plugin_folder_is_ignored(ctx, tmp_path):
    make_plugin(tmp_path, "sneaky", icon="../../../etc/passwd")
    r = registry(ctx, [tmp_path])
    assert r.get("sneaky").manifest.icon_path is None

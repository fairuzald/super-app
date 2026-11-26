from pathlib import Path

from conftest import FakeLauncher

from superapp_core import AppPlugin, PluginContext, PluginManifest, ToolStatus


class Tool(AppPlugin):
    app_id = "org.example.Tool"
    script = "app/tool.py"

    def status(self):
        return ToolStatus("s", "h", "c")


def tool(ctx, tmp_path, installed=True) -> Tool:
    if installed:
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "tool.py").write_text("")
    man = PluginManifest.from_dict({"id": "tool", "name": "Tool", "entry": "plugin.py:Tool"}, Path(tmp_path))
    return Tool(man, ctx)


def test_not_installed_notifies(ctx, tmp_path):
    t = tool(ctx, tmp_path, installed=False)
    t.open("x")
    assert ctx.launcher.calls == [] and "isn’t installed" in ctx.notifier.messages[0]


def test_running_tool_gets_action_over_bridge(ctx, tmp_path):
    t = tool(ctx, tmp_path)
    ctx.bridge.running.add(t.app_id)
    t.open("do-it")
    assert ctx.bridge.activated == [(t.app_id, "do-it")] and ctx.launcher.calls == []


def test_running_tool_without_action_is_raised(ctx, tmp_path):
    t = tool(ctx, tmp_path)
    ctx.bridge.running.add(t.app_id)
    t.open()
    assert ctx.launcher.calls == [["python3", str(tmp_path / "app" / "tool.py")]]


def test_closed_tool_is_started_then_given_the_action(ctx, tmp_path):
    launcher = FakeLauncher(starts=lambda: ctx.bridge.running.add("org.example.Tool"))
    c = PluginContext(**{**ctx.__dict__, "launcher": launcher})
    t = tool(c, tmp_path)
    t.open("do-it")
    assert len(launcher.calls) == 1
    assert ctx.bridge.activated == [("org.example.Tool", "do-it")]


def test_launch_failure_is_reported(ctx, tmp_path):
    c = PluginContext(**{**ctx.__dict__, "launcher": FakeLauncher(ok=False)})
    t = tool(c, tmp_path)
    t.open("x")
    assert "Couldn’t open" in ctx.notifier.messages[-1] and ctx.bridge.activated == []

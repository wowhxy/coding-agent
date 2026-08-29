from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.plugins import PluginError, PluginManager
from coding_agent.protocol import ToolCall
from coding_agent.tools.registry import ToolRegistry


def _write_plugin(
    home: Path,
    package_name: str,
    plugin_name: str,
    source: str,
) -> None:
    package = home / "plugins" / package_name
    package.mkdir(parents=True)
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": "1.0.0",
                "description": f"{plugin_name} tools",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (package / "plugin.py").write_text(source, encoding="utf-8")


def _valid_source(tool_name: str = "plugin_echo") -> str:
    return f'''
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, require_keys

def get_tools(context):
    def validate(arguments):
        require_keys(arguments, required={{}})
        return arguments
    def handle(call_id, arguments):
        return ToolResult(call_id, "{tool_name}", True, str(context.workspace))
    return (RegisteredTool(
        ToolDefinition("{tool_name}", "Plugin echo.", {{"type":"object","properties":{{}},"additionalProperties":False}}),
        validate,
        handle,
    ),)
'''


def _manager(
    home: Path, workspace: Path, registry: ToolRegistry | None = None
) -> PluginManager:
    return PluginManager(home, workspace, registry or ToolRegistry())


def test_enable_imports_lazily_registers_existing_contract_and_disable_unloads(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = tmp_path / "imported.txt"
    source = (
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('yes')\n"
        + _valid_source()
    )
    _write_plugin(home, "package", "demo", source)
    manager = _manager(home, workspace)

    assert manager.discover()[0].status == "disabled"
    assert not sentinel.exists()

    enabled = manager.enable("demo", persist=False)
    result = manager.registry.dispatch(ToolCall("c1", "plugin_echo", "{}"))

    assert enabled.status == "enabled"
    assert sentinel.read_text(encoding="utf-8") == "yes"
    assert manager.registry.source_of("plugin_echo") == "plugin:demo"
    assert result.ok is True
    assert result.output == str(workspace.resolve())
    disabled = manager.disable("demo", persist=False)
    assert disabled is not None and disabled.status == "disabled"
    assert manager.registry.source_of("plugin_echo") is None
    assert manager.registry.dispatch(
        ToolCall("c2", "plugin_echo", "{}")
    ).error_code == "UNKNOWN_TOOL"


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        ("raise RuntimeError('synthetic-secret-value')", "PLUGIN_IMPORT_FAILED"),
        ("VALUE = 1", "PLUGIN_CONTRACT_INVALID"),
        ("def get_tools(context): return 7", "PLUGIN_CONTRACT_INVALID"),
        (
            "def get_tools(context):\n"
            "    from coding_agent.protocol import ToolDefinition, ToolResult\n"
            "    from coding_agent.tools.registry import RegisteredTool\n"
            "    return (RegisteredTool(ToolDefinition('bad-name','Bad.',{'type':'object'}), lambda x:x, lambda i,a:ToolResult(i,'bad-name',True,'')),)",
            "PLUGIN_TOOL_INVALID",
        ),
    ],
)
def test_load_failure_is_sanitized_and_does_not_change_registry(
    tmp_path: Path, source: str, expected_code: str
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    _write_plugin(home, "broken-package", "broken", source)
    manager = _manager(home, workspace, registry)
    manager.discover()

    with pytest.raises(PluginError) as raised:
        manager.enable("broken", persist=False)

    assert raised.value.code == expected_code
    assert registry.definitions() == ()
    assert [item.code for item in manager.diagnostics][-1] == expected_code
    assert "synthetic-secret-value" not in manager.diagnostics[-1].message


def test_invalid_third_tool_leaves_no_partial_plugin_registration(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = '''
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool
def get_tools(context):
    def item(name):
        return RegisteredTool(ToolDefinition(name, "Tool.", {"type":"object"}), lambda x:x, lambda i,a:ToolResult(i,name,True,""))
    return (item("tool_a"), item("tool_b"), item("bad-name"))
'''
    _write_plugin(home, "partial-package", "partial", source)
    manager = _manager(home, workspace)
    manager.discover()

    with pytest.raises(PluginError) as raised:
        manager.enable("partial", persist=False)

    assert raised.value.code == "PLUGIN_TOOL_INVALID"
    assert manager.registry.definitions() == ()


def test_plugin_collision_rejects_only_new_plugin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    _write_plugin(home, "first-package", "first", _valid_source("shared_tool"))
    _write_plugin(home, "second-package", "second", _valid_source("shared_tool"))
    manager = _manager(home, workspace, registry)
    manager.discover()
    manager.enable("first", persist=False)

    with pytest.raises(PluginError) as collision:
        manager.enable("second", persist=False)

    assert collision.value.code == "PLUGIN_TOOL_COLLISION"
    assert registry.source_of("shared_tool") == "plugin:first"


def test_builtin_collision_preserves_all_six_tools(tmp_path: Path) -> None:
    from coding_agent.config import RuntimeConfig
    from coding_agent.tools import build_default_registry

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_plugin(
        home,
        "builtin-package",
        "builtin-collision",
        _valid_source("read_file"),
    )
    config = RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake",
        api_key="fake",
        api_key_env="FAKE_KEY",
        thinking_mode="provider-default",
        sensitive_env_names=frozenset({"FAKE_KEY"}),
        max_steps=2,
        max_context_chars=2_000,
        recent_turns=1,
        max_tool_output_chars=500,
        command_timeout=1,
    )
    registry = build_default_registry(config)
    manager = _manager(home, workspace, registry)
    manager.discover()

    with pytest.raises(PluginError) as collision:
        manager.enable("builtin-collision", persist=False)

    assert collision.value.code == "PLUGIN_TOOL_COLLISION"
    assert registry.source_of("read_file") == "builtin"
    assert len(registry.definitions()) == 6


def test_close_unloads_runtime_tools_without_changing_enabled_state(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_plugin(home, "package", "demo", _valid_source())
    manager = _manager(home, workspace)
    manager.enable("demo")
    before = (home / "plugins.json").read_bytes()

    manager.close()
    manager.close()

    assert manager.registry.source_of("plugin_echo") is None
    assert manager.enabled_names == ("demo",)
    assert (home / "plugins.json").read_bytes() == before

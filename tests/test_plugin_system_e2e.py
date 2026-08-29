from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager
from coding_agent.plugins import PluginError, PluginManager
from coding_agent.protocol import ModelTurn, RunStatus, ToolCall
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from tests.fakes import FakeModelClient


EXAMPLE = Path("examples/plugins/git-readonly")
BUILTIN_NAMES = (
    "list_files",
    "search_text",
    "read_file",
    "write_file",
    "replace_in_file",
    "execute_command",
)


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake",
        api_key="fake",
        api_key_env="FAKE_API_KEY",
        thinking_mode="disabled",
        sensitive_env_names=frozenset({"FAKE_API_KEY"}),
        max_steps=8,
        max_context_chars=20_000,
        recent_turns=4,
        max_tool_output_chars=2_000,
        command_timeout=5,
    )


def _install_example(home: Path) -> None:
    shutil.copytree(EXAMPLE, home / "plugins" / "git-readonly")


def _initialize_repository(workspace: Path) -> None:
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "plugin-e2e@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Plugin E2E"],
        cwd=workspace,
        check=True,
    )
    (workspace / "calculator.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "calculator.py"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    (workspace / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )


def test_plugin_lifecycle_runs_real_tools_through_unmodified_agent_loop(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_example(home)
    _initialize_repository(workspace)
    registry = build_default_registry(_config(workspace))
    manager = PluginManager(home, workspace, registry)

    assert registry.dispatch(
        ToolCall("disabled", "git_status", "{}")
    ).error_code == "UNKNOWN_TOOL"
    manager.enable("git-readonly")
    assert manager.enabled_names == ("git-readonly",)

    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("status", "git_status", "{}"),)),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "diff",
                        "git_diff",
                        json.dumps({"path": "calculator.py"}),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "read", "read_file", '{"path":"calculator.py"}'
                    ),
                )
            ),
            ModelTurn("Plugin and built-in inspection complete."),
        ]
    )
    result = AgentRunner(model, registry, ContextManager()).run(
        SYSTEM_PROMPT, "Inspect the current change using Git and read the file."
    )

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.steps == 4
    assert tuple(item.name for item in model.calls[0][1]) == (
        *BUILTIN_NAMES,
        "git_status",
        "git_diff",
        "git_log",
    )
    feedback = "\n".join(
        message.content or ""
        for messages, _definitions in model.calls[1:]
        for message in messages
    )
    assert "calculator.py" in feedback
    assert "return left - right" in feedback

    manager.close()
    restored_registry = build_default_registry(_config(workspace))
    restored = PluginManager(home, workspace, restored_registry)
    assert [item.metadata.name for item in restored.restore_enabled()] == [
        "git-readonly"
    ]
    assert restored_registry.source_of("git_status") == "plugin:git-readonly"

    restored.disable("git-readonly")
    assert tuple(item.name for item in restored_registry.definitions()) == BUILTIN_NAMES
    next_model = FakeModelClient([ModelTurn("No plugin tool is active.")])
    AgentRunner(next_model, restored_registry, ContextManager()).run(
        SYSTEM_PROMPT, "Continue without the plugin."
    )
    assert tuple(item.name for item in next_model.calls[0][1]) == BUILTIN_NAMES

    final_registry = build_default_registry(_config(workspace))
    final_manager = PluginManager(home, workspace, final_registry)
    assert final_manager.restore_enabled() == ()
    assert final_manager.enabled_names == ()


def test_broken_home_plugin_and_workspace_code_are_safely_isolated(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_example(home)
    broken = home / "plugins" / "broken"
    broken.mkdir()
    (broken / "plugin.json").write_text(
        json.dumps(
            {
                "name": "broken",
                "version": "1.0.0",
                "description": "Broken plugin",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (broken / "plugin.py").write_text(
        "raise RuntimeError('synthetic private import detail')\n", encoding="utf-8"
    )
    workspace_plugin = workspace / "plugins" / "untrusted"
    workspace_plugin.mkdir(parents=True)
    marker = workspace / "workspace-plugin-imported"
    (workspace_plugin / "plugin.json").write_text(
        json.dumps(
            {
                "name": "untrusted",
                "version": "1.0.0",
                "description": "Must not be discovered",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (workspace_plugin / "plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    registry = build_default_registry(_config(workspace))
    manager = PluginManager(home, workspace, registry)

    assert [item.metadata.name for item in manager.discover()] == [
        "broken",
        "git-readonly",
    ]
    manager.enable("git-readonly", persist=False)
    with pytest.raises(PluginError) as captured:
        manager.enable("broken", persist=False)

    assert captured.value.code == "PLUGIN_IMPORT_FAILED"
    assert "synthetic private import detail" not in captured.value.message
    assert not marker.exists()
    assert tuple(item.name for item in registry.definitions()) == (
        *BUILTIN_NAMES,
        "git_status",
        "git_diff",
        "git_log",
    )

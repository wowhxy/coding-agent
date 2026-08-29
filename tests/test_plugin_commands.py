from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.cli import main
from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.plugins import PluginManager
from coding_agent.protocol import Message, ModelTurn, Role, RunResult, RunStatus, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


def _source(tool_name: str) -> str:
    return f'''
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, require_keys
def get_tools(context):
    def validate(arguments):
        require_keys(arguments, required={{}})
        return arguments
    def handle(call_id, arguments):
        return ToolResult(call_id, "{tool_name}", True, "plugin ok")
    return (RegisteredTool(ToolDefinition("{tool_name}", "Echo from plugin.", {{"type":"object","properties":{{}},"additionalProperties":False}}), validate, handle),)
'''


def _install(home: Path, name: str = "demo", source: str | None = None) -> None:
    package = home / "plugins" / f"{name}-package"
    package.mkdir(parents=True)
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "description": "Demonstration tools",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (package / "plugin.py").write_text(
        source or _source("plugin_echo"),
        encoding="utf-8",
    )


def test_enabled_state_is_sorted_idempotent_and_restored_after_restart(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home, "zeta", _source("zeta_tool"))
    _install(home, "alpha", _source("alpha_tool"))
    manager = PluginManager(home, workspace, ToolRegistry())

    manager.enable("zeta")
    manager.enable("alpha")
    manager.enable("alpha")

    assert json.loads((home / "plugins.json").read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "enabled": ["alpha", "zeta"],
    }
    restarted = PluginManager(home, workspace, ToolRegistry())
    restored = restarted.restore_enabled()
    assert [item.metadata.name for item in restored] == ["alpha", "zeta"]
    assert restarted.enabled_names == ("alpha", "zeta")
    assert [item.name for item in restarted.registry.definitions()] == [
        "alpha_tool",
        "zeta_tool",
    ]


@pytest.mark.parametrize(
    "payload",
    ["{", '{"schema_version":99,"enabled":[]}', '{"schema_version":1,"enabled":"demo"}'],
)
def test_corrupt_or_unsupported_state_falls_back_without_loading(
    tmp_path: Path, payload: str
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home)
    home.mkdir(exist_ok=True)
    (home / "plugins.json").write_text(payload, encoding="utf-8")
    manager = PluginManager(home, workspace, ToolRegistry())

    assert manager.restore_enabled() == ()
    assert manager.enabled_names == ()
    assert manager.registry.definitions() == ()
    assert manager.diagnostics[-1].code == "PLUGIN_STATE_INVALID"


def test_missing_or_broken_enabled_plugin_is_diagnostic_and_core_continues(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home, "broken", "raise RuntimeError('private failure detail')")
    home.mkdir(exist_ok=True)
    (home / "plugins.json").write_text(
        json.dumps(
            {"schema_version": 1, "enabled": ["broken", "missing"]}
        ),
        encoding="utf-8",
    )
    manager = PluginManager(home, workspace, ToolRegistry())

    assert manager.restore_enabled() == ()
    assert manager.enabled_names == ("broken", "missing")
    codes = {item.code for item in manager.diagnostics}
    assert "PLUGIN_IMPORT_FAILED" in codes
    assert "PLUGIN_ENABLED_MISSING" in codes
    assert "private failure detail" not in " ".join(
        item.message for item in manager.diagnostics
    )

    assert manager.disable("missing") is None
    assert json.loads((home / "plugins.json").read_text(encoding="utf-8"))[
        "enabled"
    ] == ["broken"]


class _Runner:
    def run_turn(self, history: ConversationHistory, task: str) -> RunResult:
        history.append(Message(Role.USER, task))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)


def test_interactive_plugin_commands_list_enable_and_disable_idempotently(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home)
    store = JsonSessionStore(home, id_generator=lambda: "111111111111")
    record = store.create_session(workspace, "fake", "model")
    manager = PluginManager(home, workspace, ToolRegistry())
    manager.discover()
    commands = iter(
        (
            "/plugins",
            "/plugin enable demo",
            "/plugin enable demo",
            "/plugins",
            "/plugin disable demo",
            "/plugin disable demo",
            "/plugins",
            "/exit",
        )
    )
    output: list[str] = []

    assert InteractiveShell(
        InteractiveSession(
            _Runner(),  # type: ignore[arg-type]
            ConversationHistory("system"),
            record,
            store,
            "fake",
            "model",
            (),
        ),
        store,
        lambda _prompt: next(commands),
        output.append,
        plugin_manager=manager,
    ).run() == 0

    assert output.count("NAME VERSION STATUS DESCRIPTION") == 3
    assert any("demo  1.0.0  enabled  Demonstration tools" in line for line in output)
    assert any("demo  1.0.0  disabled  Demonstration tools" in line for line in output)
    assert manager.registry.source_of("plugin_echo") is None
    assert json.loads((home / "plugins.json").read_text(encoding="utf-8"))[
        "enabled"
    ] == []


def test_plugin_listing_redacts_sensitive_manifest_description(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "synthetic-provider-secret"
    _install(home)
    manifest_path = home / "plugins" / "demo-package" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["description"] = f"Tools using {secret}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = JsonSessionStore(home, id_generator=lambda: "111111111111")
    record = store.create_session(workspace, "fake", "model")
    manager = PluginManager(home, workspace, ToolRegistry())
    output: list[str] = []
    commands = iter(("/plugins", "/exit"))

    assert InteractiveShell(
        InteractiveSession(
            _Runner(),  # type: ignore[arg-type]
            ConversationHistory("system"),
            record,
            store,
            "fake",
            "model",
            (secret,),
        ),
        store,
        lambda _prompt: next(commands),
        output.append,
        plugin_manager=manager,
    ).run() == 0

    rendered = "\n".join(output)
    assert secret not in rendered
    assert "[REDACTED]" in rendered


def test_one_shot_restores_enabled_plugin_before_agent_loop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home)
    PluginManager(home, workspace, ToolRegistry()).enable("demo")
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("p1", "plugin_echo", "{}"),)),
            ModelTurn("done"),
        ]
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--base-url",
            "https://example.test/v1",
            "--model",
            "fake",
            "task",
        ],
        environ={
            "OPENAI_API_KEY": "fake-test-key",
            "CODING_AGENT_HOME": str(home),
        },
        client_factory=lambda *_args: model,
    )

    assert exit_code == 0
    assert any(
        definition.name == "plugin_echo" for definition in model.calls[0][1]
    )

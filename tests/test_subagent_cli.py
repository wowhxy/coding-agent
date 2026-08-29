from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterable
from pathlib import Path

import pytest

from coding_agent.cli import main
from coding_agent.config import resolve_config
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.plugins import PluginManager
from coding_agent.protocol import ModelTurn, Role, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.tools import build_default_registry
from fakes import FakeModelClient


API_KEY = "fake-subagent-cli-provider-key"


class ClosableFakeModelClient(FakeModelClient):
    def __init__(self, script: Iterable[ModelTurn | Exception]) -> None:
        super().__init__(script)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class QueueClientFactory:
    def __init__(self, *clients: ClosableFakeModelClient) -> None:
        self._clients = list(clients)
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str, str, str]] = []

    def __call__(
        self, base_url: str, model: str, api_key: str, thinking_mode: str
    ) -> ClosableFakeModelClient:
        with self._lock:
            self.calls.append((base_url, model, api_key, thinking_mode))
            if not self._clients:
                raise AssertionError("unexpected model client creation")
            return self._clients.pop(0)


def _arguments(workspace: Path, *tail: str) -> list[str]:
    return [
        "--workspace",
        str(workspace),
        "--base-url",
        "https://example.test/v1",
        "--model",
        "fake-model",
        *tail,
    ]


def _environment(home: Path) -> dict[str, str]:
    return {"OPENAI_API_KEY": API_KEY, "CODING_AGENT_HOME": str(home)}


def _write_skill(home: Path) -> None:
    path = home / "skills" / "inspect-project" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: inspect-project\n"
        "description: Inspect parser structure.\n---\n\n"
        "SKILL_PRIVATE_GUIDANCE: inspect call sites before conclusions.\n",
        encoding="utf-8",
    )


def _enable_example_plugin(home: Path, workspace: Path) -> None:
    source = Path(__file__).parents[1] / "examples" / "plugins" / "git-readonly"
    shutil.copytree(source, home / "plugins" / "git-readonly")
    config = resolve_config(
        workspace=workspace,
        base_url="https://example.test/v1",
        model="fake-model",
        environ={"OPENAI_API_KEY": API_KEY},
    )
    manager = PluginManager(home, workspace, build_default_registry(config))
    manager.enable("git-readonly")
    manager.close()


def _message_text(client: FakeModelClient, call_index: int = 0) -> str:
    return "\n".join(
        message.content or "" for message in client.calls[call_index][0]
    )


def test_one_shot_cli_composes_memory_skill_plugin_and_safe_subagent_events(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    WorkspaceMemoryStore(home).add(
        workspace,
        "src/parser.py",
        (),
        kind="architecture",
        key="source.root",
    )
    _write_skill(home)
    _enable_example_plugin(home, workspace)
    parent = ClosableFakeModelClient(
        [
            ModelTurn('{"skills":["inspect-project"]}'),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "delegate",
                        "delegate_tasks",
                        json.dumps(
                            {
                                "tasks": [
                                    {"task": "Inspect parser memory and guidance"}
                                ]
                            }
                        ),
                    ),
                )
            ),
            ModelTurn("delegation complete"),
        ]
    )
    child = ClosableFakeModelClient(
        [ModelTurn(f"CHILD_PRIVATE_RESULT {API_KEY}")]
    )
    factory = QueueClientFactory(parent, child)

    exit_code = main(
        _arguments(workspace, "inspect parser"),
        environ=_environment(home),
        client_factory=factory,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert len(factory.calls) == 2
    assert parent.closed and child.closed
    parent_definitions = tuple(item.name for item in parent.calls[1][1])
    assert "delegate_tasks" in parent_definitions
    assert "git_status" in parent_definitions
    assert tuple(item.name for item in child.calls[0][1]) == (
        "list_files",
        "search_text",
        "read_file",
    )
    child_text = _message_text(child)
    assert "src/parser.py" in child_text
    assert "SKILL_PRIVATE_GUIDANCE" in child_text
    assert "git_status" not in child_text
    parent_after_delegation = _message_text(parent, 2)
    assert API_KEY not in parent_after_delegation
    assert "[REDACTED]" in parent_after_delegation
    assert "[subagents] batch started: 1" in output.out
    assert "[subagent subagent-1] running: explore" in output.out
    assert "[subagent subagent-1] completed: FINAL_RESPONSE" in output.out
    assert "[subagents] collected: 1" in output.out
    assert "CHILD_PRIVATE_RESULT" not in output.out
    assert API_KEY not in output.out + output.err


def test_interactive_delegation_persists_only_parent_protocol_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    parent = ClosableFakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-delegate",
                        "delegate_tasks",
                        '{"tasks":[{"task":"child ephemeral task"}]}',
                    ),
                )
            ),
            ModelTurn("interactive done"),
            ModelTurn('{"candidates":[]}'),
        ]
    )
    child = ClosableFakeModelClient([ModelTurn("child finding")])
    factory = QueueClientFactory(parent, child)
    inputs = iter(("inspect interactively", "/exit"))

    exit_code = main(
        _arguments(workspace),
        environ=_environment(home),
        client_factory=factory,
        input_reader=lambda _prompt: next(inputs),
    )

    assert exit_code == 0
    assert parent.closed and child.closed
    store = JsonSessionStore(home)
    summaries = store.list_sessions(workspace)
    assert len(summaries) == 1
    saved = store.load_session(summaries[0].session_id, workspace)
    assert tuple(message.role for message in saved.messages) == (
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    )
    assert sum(
        len(message.tool_calls)
        for message in saved.messages
        if message.role is Role.ASSISTANT
    ) == 1
    assert "child finding" in "\n".join(
        message.content or "" for message in saved.messages
    )
    assert "child tool trace" not in "\n".join(
        message.content or "" for message in saved.messages
    )

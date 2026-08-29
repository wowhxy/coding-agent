from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import resolve_config
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.protocol import (
    Message,
    ModelTurn,
    Role,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from coding_agent.subagents.control import create_delegate_tasks_tool
from coding_agent.subagents.manager import SubagentManager
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from fakes import FakeModelClient


def _python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


class InvestigatingClient(FakeModelClient):
    """Choose one deterministic read plan from the delegated task text."""

    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__([])
        self._barrier = barrier
        self.closed = False

    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        self.calls.append((tuple(messages), tuple(tool_definitions)))
        delegated = next(
            message.content or "" for message in messages if message.role is Role.USER
        )
        task_line = delegated.splitlines()[0]
        if len(self.calls) == 1:
            self._barrier.wait(timeout=5)
            if "implementation" in task_line:
                return ModelTurn(
                    tool_calls=(
                        ToolCall("child-read-parser", "read_file", '{"path":"parser.py"}'),
                    )
                )
            if "tests" in task_line:
                return ModelTurn(
                    tool_calls=(
                        ToolCall("child-read-tests", "read_file", '{"path":"test_parser.py"}'),
                    )
                )
            return ModelTurn(
                tool_calls=(
                    ToolCall("child-read-callsite", "read_file", '{"path":"app.py"}'),
                    ToolCall(
                        "child-write-denied",
                        "write_file",
                        '{"path":"evil.txt","content":"forbidden"}',
                    ),
                    ToolCall(
                        "child-replace-denied",
                        "replace_in_file",
                        '{"path":"parser.py","old_text":"x","new_text":"y"}',
                    ),
                    ToolCall(
                        "child-command-denied",
                        "execute_command",
                        '{"command":"echo forbidden"}',
                    ),
                    ToolCall(
                        "child-delegate-denied",
                        "delegate_tasks",
                        '{"tasks":[{"task":"nested"}]}',
                    ),
                )
            )
        if "implementation" in task_line:
            return ModelTurn("Implementation performs an unnecessary ASCII round-trip.")
        if "tests" in task_line:
            return ModelTurn("The Unicode parser test expects the original characters.")
        return ModelTurn("app.py calls parse_pair directly; no API change is needed.")

    def close(self) -> None:
        self.closed = True


def _tool_payloads(messages: tuple[Message, ...]) -> dict[str, dict[str, object]]:
    return {
        message.tool_call_id: json.loads(message.content or "{}")
        for message in messages
        if message.role is Role.TOOL and message.tool_call_id is not None
    }


def test_parallel_read_only_children_support_parent_only_parser_repair(
    tmp_path: Path,
) -> None:
    (tmp_path / "parser.py").write_text(
        "def parse_pair(text: str) -> tuple[str, str]:\n"
        "    normalized = text.encode('ascii').decode('ascii')\n"
        "    left, right = normalized.split(':', 1)\n"
        "    return left, right\n",
        encoding="utf-8",
    )
    (tmp_path / "test_parser.py").write_text(
        "from parser import parse_pair\n\n"
        "def test_unicode_pair():\n"
        "    assert parse_pair('名字:值') == ('名字', '值')\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "from parser import parse_pair\n\n"
        "def display(raw: str) -> str:\n"
        "    left, right = parse_pair(raw)\n"
        "    return f'{left}={right}'\n",
        encoding="utf-8",
    )
    test_command = _python_command("-m", "pytest", "-q")
    parent = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-test-before",
                        "execute_command",
                        json.dumps({"command": test_command}),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-delegate",
                        "delegate_tasks",
                        json.dumps(
                            {
                                "tasks": [
                                    {
                                        "task": "Inspect parser implementation",
                                        "role": "explore",
                                        "context_mode": "fresh",
                                    },
                                    {
                                        "task": "Analyze parser tests",
                                        "role": "analysis",
                                        "context_mode": "fresh",
                                    },
                                    {
                                        "task": "Review parser callsites",
                                        "role": "review",
                                        "context_mode": "fork",
                                    },
                                ]
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-edit",
                        "replace_in_file",
                        json.dumps(
                            {
                                "path": "parser.py",
                                "old_text": (
                                    "    normalized = text.encode('ascii').decode('ascii')"
                                ),
                                "new_text": "    normalized = text",
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-test-after",
                        "execute_command",
                        json.dumps({"command": test_command}),
                    ),
                )
            ),
            ModelTurn("Fixed Unicode parsing and verified the regression test."),
        ]
    )
    barrier = threading.Barrier(3)
    child_clients: list[InvestigatingClient] = []
    factory_lock = threading.Lock()

    def create_child() -> InvestigatingClient:
        with factory_lock:
            client = InvestigatingClient(barrier)
            child_clients.append(client)
            return client

    manager = SubagentManager(tmp_path, create_child)
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="fake",
        environ={"OPENAI_API_KEY": "fake-e2e-key"},
    )
    registry = build_default_registry(config)
    registry.register_many(
        (create_delegate_tasks_tool(manager),), source="control:subagent"
    )
    runner = AgentRunner(
        parent,
        registry,
        ContextManager(),
        run_start_hook=manager.begin_parent_run,
        context_snapshot_sink=manager.observe_parent_context,
    )
    history = ConversationHistory(SYSTEM_PROMPT)

    result = runner.run_turn(history, "Repair the Unicode parser failure.")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.steps == 5
    assert len(child_clients) == 3
    assert all(client.closed for client in child_clients)
    assert all(
        tuple(item.name for item in client.calls[0][1])
        == ("list_files", "search_text", "read_file")
        for client in child_clients
    )
    delegated_inputs = {
        next(
            message.content or ""
            for message in client.calls[0][0]
            if message.role is Role.USER
        ): client
        for client in child_clients
    }
    assert sum("Bounded parent context snapshot" in text for text in delegated_inputs) == 1
    assert any(
        text.startswith("Review parser callsites")
        and "Bounded parent context snapshot" in text
        for text in delegated_inputs
    )
    callsite_client = next(
        client
        for text, client in delegated_inputs.items()
        if text.startswith("Review parser callsites")
    )
    denied = _tool_payloads(callsite_client.calls[1][0])
    assert {
        denied[call_id]["error_code"]
        for call_id in (
            "child-write-denied",
            "child-replace-denied",
            "child-command-denied",
            "child-delegate-denied",
        )
    } == {"UNKNOWN_TOOL"}
    assert not (tmp_path / "evil.txt").exists()

    parent_payloads = _tool_payloads(history.messages)
    assert parent_payloads["parent-test-before"]["error_code"] == "COMMAND_FAILED"
    assert parent_payloads["parent-edit"]["ok"] is True
    assert parent_payloads["parent-test-after"]["ok"] is True
    delegation = json.loads(str(parent_payloads["parent-delegate"]["output"]))
    assert [item["role"] for item in delegation["results"]] == [
        "explore",
        "analysis",
        "review",
    ]
    assert {item["status"] for item in delegation["results"]} == {
        "FINAL_RESPONSE"
    }
    parent_text = "\n".join(message.content or "" for message in history.messages)
    assert "child-read-parser" not in parent_text
    assert "child-read-tests" not in parent_text
    assert "child-read-callsite" not in parent_text
    assert "encode('ascii')" not in (tmp_path / "parser.py").read_text(
        encoding="utf-8"
    )

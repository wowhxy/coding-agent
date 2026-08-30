from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import resolve_config
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.protocol import (
    AgentEvent,
    ModelTurn,
    Role,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from coding_agent.tools.registry import (
    RegisteredTool,
    ToolEffect,
    ToolRegistry,
)
from coding_agent.tools import build_default_registry
from fakes import FakeModelClient


def test_agent_runner_parallelizes_one_model_turn_and_preserves_feedback_order() -> None:
    """Catches a scheduler that exists but is not used by AgentRunner."""

    first_started = threading.Event()
    second_started = threading.Event()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        if call_id == "call-a":
            first_started.set()
            overlapped = second_started.wait(timeout=1)
        else:
            second_started.set()
            overlapped = first_started.wait(timeout=1)
        return ToolResult(call_id, "read_data", True, str(overlapped))

    registry = ToolRegistry()
    registry.register_many(
        (
            RegisteredTool(
                ToolDefinition(
                    "read_data",
                    "Read independent test data.",
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ),
                lambda arguments: arguments,
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("call-a", "read_data", "{}"),
                    ToolCall("call-b", "read_data", "{}"),
                )
            ),
            ModelTurn("done"),
        ]
    )
    events: list[AgentEvent] = []
    history = ConversationHistory("system")

    runner = AgentRunner(
        model,
        registry,
        ContextManager(),
        event_sink=events.append,
    )
    result = runner.run_turn(history, "inspect both")

    assert result.status is RunStatus.FINAL_RESPONSE
    tool_messages = tuple(
        message for message in history.messages if message.role is Role.TOOL
    )
    assert tuple(message.tool_call_id for message in tool_messages) == (
        "call-a",
        "call-b",
    )
    assert tuple(
        json.loads(message.content or "{}")["output"] for message in tool_messages
    ) == ("True", "True")
    starts = tuple(event for event in events if event.kind == "tool_requested")
    finishes = tuple(event for event in events if event.kind == "tool_result")
    assert tuple(event.tool_call_id for event in starts) == ("call-a", "call-b")
    assert tuple(event.tool_call_id for event in finishes) == ("call-a", "call-b")
    assert runner.last_tool_execution_stats.tool_calls_total == 2
    assert runner.last_tool_execution_stats.parallel_groups == 1
    assert runner.last_tool_execution_stats.parallel_calls == 2
    assert runner.last_tool_execution_stats.serial_calls == 0


def test_cancelled_parallel_turn_does_not_append_partial_protocol_history() -> None:
    """Catches cancellation persisting an assistant batch without all results."""

    both_started = threading.Barrier(3)
    release = threading.Event()
    cancelled = threading.Event()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        both_started.wait(timeout=2)
        assert release.wait(timeout=2)
        return ToolResult(call_id, "read_data", True, call_id)

    registry = ToolRegistry()
    registry.register_many(
        (
            RegisteredTool(
                ToolDefinition("read_data", "Read.", {"type": "object"}),
                lambda arguments: arguments,
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    runner = AgentRunner(
        FakeModelClient(
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall("a", "read_data", "{}"),
                        ToolCall("b", "read_data", "{}"),
                    )
                )
            ]
        ),
        registry,
        ContextManager(),
    )
    history = ConversationHistory("system")
    result_holder = []
    worker = threading.Thread(
        target=lambda: result_holder.append(
            runner.run_turn(history, "inspect", cancel_check=cancelled.is_set)
        )
    )
    worker.start()
    both_started.wait(timeout=2)
    cancelled.set()
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result_holder[0].status is RunStatus.CANCELLED
    assert tuple(message.role for message in history.messages) == (
        Role.SYSTEM,
        Role.USER,
    )


def test_parallel_read_edit_verify_agent_e2e(tmp_path: Path) -> None:
    """Catches loss of ordering across real read, mutation, and command tools."""

    source = tmp_path / "src" / "parser.py"
    test_source = tmp_path / "tests" / "test_parser.py"
    source.parent.mkdir()
    test_source.parent.mkdir()
    source.write_text("VALUE = 'old'\n", encoding="utf-8")
    test_source.write_text("from src.parser import VALUE\n", encoding="utf-8")
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="fake",
        environ={"OPENAI_API_KEY": "fake-key"},
    )
    verify_command = _python_command(
        "-c", "from src.parser import VALUE; assert VALUE == 'fixed'"
    )
    model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "read-source", "read_file", '{"path":"src/parser.py"}'
                    ),
                    ToolCall(
                        "read-test",
                        "read_file",
                        '{"path":"tests/test_parser.py"}',
                    ),
                    ToolCall(
                        "search",
                        "search_text",
                        '{"query":"VALUE","path":"."}',
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "replace_in_file",
                        json.dumps(
                            {
                                "path": "src/parser.py",
                                "old_text": "VALUE = 'old'",
                                "new_text": "VALUE = 'fixed'",
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "verify",
                        "execute_command",
                        json.dumps({"command": verify_command}),
                    ),
                )
            ),
            ModelTurn("fixed and verified"),
        ]
    )
    runner = AgentRunner(model, build_default_registry(config), ContextManager())
    history = ConversationHistory("system")

    result = runner.run_turn(history, "inspect, fix, and verify")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert source.read_text(encoding="utf-8") == "VALUE = 'fixed'\n"
    assert tuple(
        message.tool_call_id
        for message in history.messages
        if message.role is Role.TOOL
    ) == ("read-source", "read-test", "search", "edit", "verify")
    stats = runner.last_tool_execution_stats
    assert (
        stats.tool_calls_total,
        stats.parallel_groups,
        stats.parallel_calls,
        stats.serial_calls,
    ) == (5, 1, 3, 2)


def _python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)

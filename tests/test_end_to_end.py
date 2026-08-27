from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import resolve_config
from coding_agent.context import ContextManager
from coding_agent.protocol import (
    AgentEvent,
    Message,
    ModelTurn,
    Role,
    RunStatus,
    ToolCall,
)
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from tests.fakes import FakeModelClient


def python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _tool_results(messages: tuple[Message, ...]) -> dict[str, dict[str, object]]:
    return {
        message.tool_call_id: json.loads(message.content)
        for message in messages
        if message.role is Role.TOOL
        and message.tool_call_id is not None
        and message.content is not None
    }


def test_scripted_model_completes_real_local_edit_and_verification_loop(
    tmp_path: Path,
) -> None:
    source = tmp_path / "calculator.py"
    verification = tmp_path / "verify.py"
    source.write_text(
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    verification.write_text(
        "from calculator import add\n\n"
        "assert add(2, 3) == 5\n"
        "print('verification passed')\n",
        encoding="utf-8",
    )
    verification_command = python_command("-B", "verify.py")
    scripted_turns = [
        ModelTurn(
            tool_calls=(
                ToolCall("list-1", "list_files", '{"path":"."}'),
            )
        ),
        ModelTurn(
            tool_calls=(
                ToolCall(
                    "read-source",
                    "read_file",
                    '{"path":"calculator.py"}',
                ),
                ToolCall(
                    "read-verification",
                    "read_file",
                    '{"path":"verify.py"}',
                ),
            )
        ),
        ModelTurn(
            tool_calls=(
                ToolCall(
                    "verify-before",
                    "execute_command",
                    json.dumps({"command": verification_command}),
                ),
            )
        ),
        ModelTurn(
            tool_calls=(
                ToolCall(
                    "edit-1",
                    "replace_in_file",
                    json.dumps(
                        {
                            "path": "calculator.py",
                            "old_text": "    return left - right",
                            "new_text": "    return left + right",
                        }
                    ),
                ),
            )
        ),
        ModelTurn(
            tool_calls=(
                ToolCall(
                    "verify-after",
                    "execute_command",
                    json.dumps({"command": verification_command}),
                ),
            )
        ),
        ModelTurn(
            final_text=(
                "Changed the arithmetic implementation and ran verify.py."
            )
        ),
    ]
    client = FakeModelClient(scripted_turns)
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="test-model",
        environ={"OPENAI_API_KEY": "fake-e2e-key"},
    )
    events: list[AgentEvent] = []
    runner = AgentRunner(
        model_client=client,
        registry=build_default_registry(config),
        context_manager=ContextManager(),
        event_sink=events.append,
    )

    result = runner.run(SYSTEM_PROMPT, "Fix the failing verification.")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == (
        "Changed the arithmetic implementation and ran verify.py."
    )
    assert result.steps == 6
    assert not hasattr(result, "semantic_success")
    assert source.read_text(encoding="utf-8") == (
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
    )
    assert [
        event.message for event in events if event.kind == "tool_requested"
    ] == [
        "list_files",
        "read_file",
        "read_file",
        "execute_command",
        "replace_in_file",
        "execute_command",
    ]

    visibility = {
        1: {"list-1"},
        2: {"read-source", "read-verification"},
        3: {"verify-before"},
        4: {"edit-1"},
        5: {"verify-after"},
    }
    for call_index, expected_ids in visibility.items():
        messages, _ = client.calls[call_index]
        assert expected_ids <= _tool_results(messages).keys()

    failed_command = _tool_results(client.calls[3][0])["verify-before"]
    assert failed_command["ok"] is False
    assert failed_command["error_code"] == "COMMAND_FAILED"
    assert "status 1" in str(failed_command["error_message"])
    assert "exit_code: 1" in str(failed_command["output"])

    successful_command = _tool_results(client.calls[5][0])["verify-after"]
    assert successful_command["ok"] is True, successful_command
    assert successful_command["error_code"] is None
    assert successful_command["error_message"] is None
    assert "exit_code: 0" in str(successful_command["output"])
    assert "verification passed" in str(successful_command["output"])

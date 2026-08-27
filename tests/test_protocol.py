import json

import pytest

from coding_agent.protocol import (
    AgentEvent,
    Message,
    ModelTurn,
    Role,
    RunResult,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


def test_success_result_rejects_error_fields() -> None:
    with pytest.raises(ValueError, match="successful ToolResult"):
        ToolResult("call-1", "read_file", True, "text", "IO_ERROR", "bad")


def test_failure_result_requires_code_and_message() -> None:
    with pytest.raises(ValueError, match="failed ToolResult"):
        ToolResult("call-1", "read_file", False, "", "FILE_NOT_FOUND", None)


def test_failure_result_preserves_output_code_and_message() -> None:
    result = ToolResult(
        "call-1",
        "execute_command",
        False,
        "stdout before failure",
        "COMMAND_FAILED",
        "command exited with code 2",
    )

    assert result.output == "stdout before failure"
    assert result.error_code == "COMMAND_FAILED"
    assert result.error_message == "command exited with code 2"


def test_tool_result_serializes_all_model_facing_fields() -> None:
    result = ToolResult(
        "call-1",
        "execute_command",
        False,
        "stdout before failure",
        "COMMAND_FAILED",
        "command exited with code 2",
    )

    assert json.loads(result.as_message_content()) == {
        "ok": False,
        "output": "stdout before failure",
        "error_code": "COMMAND_FAILED",
        "error_message": "command exited with code 2",
    }


def test_protocol_types_are_constructible_with_their_public_fields() -> None:
    call = ToolCall("call-1", "read_file", '{"path":"a.py"}')
    definition = ToolDefinition("read_file", "Read a file.", {"type": "object"})
    message = Message(Role.ASSISTANT, None, (call,), None)
    turn = ModelTurn(None, (call,))
    event = AgentEvent("tool_requested", 1, "read_file")
    run = RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)

    assert definition.name == "read_file"
    assert message.tool_calls == (call,)
    assert turn.tool_calls == (call,)
    assert event.step == 1
    assert run.status is RunStatus.FINAL_RESPONSE

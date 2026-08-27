from __future__ import annotations

from typing import Any

import pytest

from coding_agent.protocol import ToolCall, ToolDefinition, ToolResult
from coding_agent.tools.registry import (
    RegisteredTool,
    ToolArgumentError,
    ToolRegistry,
    require_keys,
)


def _validate_echo(arguments: dict[str, Any]) -> dict[str, Any]:
    require_keys(arguments, required={"text"}, optional={"uppercase"})
    text = arguments["text"]
    uppercase = arguments.get("uppercase", False)
    if not isinstance(text, str):
        raise ToolArgumentError("text must be a string")
    if not isinstance(uppercase, bool):
        raise ToolArgumentError("uppercase must be a boolean")
    return {"text": text.strip(), "uppercase": uppercase}


def _handle_echo(call_id: str, arguments: dict[str, Any]) -> ToolResult:
    output = arguments["text"]
    if arguments["uppercase"]:
        output = output.upper()
    return ToolResult(call_id, "echo", True, output)


def _echo_tool(name: str = "echo") -> RegisteredTool:
    return RegisteredTool(
        ToolDefinition(
            name,
            "Return text to the caller.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "uppercase": {"type": "boolean"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        _validate_echo,
        _handle_echo,
    )


def test_dispatch_passes_normalized_arguments_and_call_id_to_handler() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(
        ToolCall("c1", "echo", '{"text":"  hello  ","uppercase":true}')
    )

    assert result == ToolResult("c1", "echo", True, "HELLO")


def test_definitions_preserve_registration_order() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool("first"))
    registry.register(_echo_tool("second"))

    assert [definition.name for definition in registry.definitions()] == [
        "first",
        "second",
    ]


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    with pytest.raises(ValueError, match="duplicate tool: echo"):
        registry.register(_echo_tool())


def test_unknown_tool_returns_recoverable_error() -> None:
    result = ToolRegistry().dispatch(ToolCall("c1", "missing", "{}"))

    assert result.ok is False
    assert result.output == ""
    assert result.error_code == "UNKNOWN_TOOL"
    assert result.error_message is not None
    assert "missing" in result.error_message


@pytest.mark.parametrize("arguments_json", ["{", "not json"])
def test_invalid_json_returns_malformed_arguments(arguments_json: str) -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(ToolCall("c1", "echo", arguments_json))

    assert result.ok is False
    assert result.output == ""
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message is not None
    assert "JSON" in result.error_message


@pytest.mark.parametrize("arguments_json", ["[]", '"text"', "null"])
def test_non_object_json_returns_malformed_arguments(arguments_json: str) -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(ToolCall("c1", "echo", arguments_json))

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message is not None
    assert "object" in result.error_message


def test_missing_required_field_returns_malformed_arguments() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(ToolCall("c1", "echo", "{}"))

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message is not None
    assert "missing required field: text" in result.error_message


def test_unknown_field_returns_malformed_arguments() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(
        ToolCall("c1", "echo", '{"text":"hello","extra":1}')
    )

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message is not None
    assert "unknown field: extra" in result.error_message


@pytest.mark.parametrize(
    ("arguments_json", "message"),
    [
        ('{"text":3}', "text must be a string"),
        ('{"text":"hello","uppercase":"yes"}', "uppercase must be a boolean"),
    ],
)
def test_wrong_field_type_returns_malformed_arguments(
    arguments_json: str, message: str
) -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    result = registry.dispatch(ToolCall("c1", "echo", arguments_json))

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message == message


def test_unexpected_handler_exception_returns_internal_error_without_traceback() -> None:
    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required=set(), optional=set())
        return arguments

    def explode(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("disk unavailable")

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            ToolDefinition("explode", "Fail unexpectedly.", {"type": "object"}),
            validate,
            explode,
        )
    )

    result = registry.dispatch(ToolCall("c9", "explode", "{}"))

    assert result.ok is False
    assert result.output == ""
    assert result.error_code == "TOOL_INTERNAL_ERROR"
    assert result.error_message is not None
    assert "explode" in result.error_message
    assert "Traceback" not in result.error_message

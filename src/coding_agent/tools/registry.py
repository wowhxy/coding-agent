"""Tool registration, argument validation, and structured dispatch errors."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..protocol import ToolCall, ToolDefinition, ToolResult


class ToolArgumentError(ValueError):
    """Raised when model-provided tool arguments fail local validation."""


ToolValidator = Callable[[dict[str, Any]], dict[str, Any]]
ToolHandler = Callable[[str, dict[str, Any]], ToolResult]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """A model-facing definition paired with local validation and execution."""

    definition: ToolDefinition
    validate: ToolValidator
    handler: ToolHandler


class ToolRegistry:
    """Store tools by name and dispatch model calls without raising tool errors."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register one uniquely named tool."""

        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        self._tools[name] = tool

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return model-facing definitions in deterministic registration order."""

        return tuple(tool.definition for tool in self._tools.values())

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Validate and execute one call, converting failures to ToolResult."""

        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError as exc:
            return _failure(
                call,
                "MALFORMED_ARGUMENTS",
                f"tool arguments are not valid JSON: {exc.msg}",
            )

        if not isinstance(arguments, dict):
            return _failure(
                call,
                "MALFORMED_ARGUMENTS",
                "tool arguments must be a JSON object",
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return _failure(call, "UNKNOWN_TOOL", f"unknown tool: {call.name}")

        try:
            normalized = tool.validate(arguments)
        except ToolArgumentError as exc:
            message = str(exc).strip() or "tool arguments failed validation"
            return _failure(call, "MALFORMED_ARGUMENTS", message)
        except Exception:
            return _failure(
                call,
                "TOOL_INTERNAL_ERROR",
                f"tool '{call.name}' validation failed unexpectedly",
            )

        try:
            return tool.handler(call.id, normalized)
        except Exception:
            return _failure(
                call,
                "TOOL_INTERNAL_ERROR",
                f"tool '{call.name}' failed unexpectedly",
            )


def require_keys(
    arguments: dict[str, Any],
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    """Reject missing required keys and keys outside the declared sets."""

    required_keys = set(required)
    allowed_keys = required_keys | set(optional)
    actual_keys = set(arguments)
    missing = sorted(required_keys - actual_keys)
    unknown = sorted(actual_keys - allowed_keys)

    problems: list[str] = []
    if missing:
        label = "field" if len(missing) == 1 else "fields"
        problems.append(f"missing required {label}: {', '.join(missing)}")
    if unknown:
        label = "field" if len(unknown) == 1 else "fields"
        problems.append(f"unknown {label}: {', '.join(unknown)}")
    if problems:
        raise ToolArgumentError("; ".join(problems))


def _failure(call: ToolCall, error_code: str, error_message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        ok=False,
        output="",
        error_code=error_code,
        error_message=error_message,
    )

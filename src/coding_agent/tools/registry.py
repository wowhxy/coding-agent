"""Tool registration, argument validation, and structured dispatch errors."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..protocol import ToolCall, ToolDefinition, ToolResult


class ToolArgumentError(ValueError):
    """Raised when model-provided tool arguments fail local validation."""


ToolValidator = Callable[[dict[str, Any]], dict[str, Any]]
ToolHandler = Callable[[str, dict[str, Any]], ToolResult]
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ToolEffect(str, Enum):
    """Execution-side effect classification used by the local scheduler."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    CONTROL = "control"


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """A model-facing definition paired with local validation and execution."""

    definition: ToolDefinition
    validate: ToolValidator
    handler: ToolHandler
    activity_kind: str = "tool"
    effect: ToolEffect = ToolEffect.MUTATING
    parallel_safe: bool = False


class ToolRegistry:
    """Store tools by name and dispatch model calls without raising tool errors."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._sources: dict[str, str] = {}
        self._historical_observations: dict[str, tuple[str, str]] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register one uniquely named tool."""

        self.register_many((tool,), source="builtin")

    def register_many(
        self,
        tools: tuple[RegisteredTool, ...],
        *,
        source: str,
    ) -> None:
        """Validate and atomically register a source-owned tool batch."""

        if type(tools) is not tuple or not tools:
            raise TypeError("tools must be a non-empty RegisteredTool tuple")
        if type(source) is not str or not source or len(source) > 128:
            raise ValueError("tool source is invalid")
        names: list[str] = []
        for tool in tools:
            _validate_registered_tool(tool)
            name = tool.definition.name
            if name in names or name in self._tools:
                raise ValueError(f"duplicate tool: {name}")
            names.append(name)
        for name, tool in zip(names, tools, strict=True):
            self._tools[name] = tool
            self._sources[name] = source
            self._historical_observations[name] = (source, tool.activity_kind)

    def unregister_source(self, source: str) -> tuple[str, ...]:
        """Remove only tools owned by a non-built-in source."""

        if source == "builtin":
            raise ValueError("built-in tools cannot be unregistered")
        removed = tuple(
            name for name in self._tools if self._sources.get(name) == source
        )
        for name in removed:
            del self._tools[name]
            del self._sources[name]
        return removed

    def source_of(self, tool_name: str) -> str | None:
        """Return the registered owner of one tool name."""

        return self._sources.get(tool_name)

    def observation_for(self, tool_name: str) -> tuple[str, str] | None:
        """Return formal product-observation metadata for one active tool."""

        tool = self._tools.get(tool_name)
        source = self._sources.get(tool_name)
        if tool is None or source is None:
            return None
        return source, tool.activity_kind

    def historical_observation_for(self, tool_name: str) -> tuple[str, str] | None:
        """Return last formal metadata for display-only historical projection."""

        return self._historical_observations.get(tool_name)

    def is_parallel_safe(self, tool_name: str) -> bool:
        """Return whether one active tool is explicitly safe for parallel reads."""

        tool = self._tools.get(tool_name)
        return bool(
            tool is not None
            and tool.effect is ToolEffect.READ_ONLY
            and tool.parallel_safe
        )

    def execution_metadata_for(
        self, tool_name: str
    ) -> tuple[ToolEffect, bool] | None:
        """Return explicit scheduling metadata for one active tool."""

        tool = self._tools.get(tool_name)
        if tool is None:
            return None
        return tool.effect, tool.parallel_safe

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


def _validate_registered_tool(tool: Any) -> None:
    if not isinstance(tool, RegisteredTool):
        raise TypeError("plugin tools must use RegisteredTool")
    definition = tool.definition
    if not isinstance(definition, ToolDefinition):
        raise TypeError("tool definition is invalid")
    if (
        type(definition.name) is not str
        or _TOOL_NAME.fullmatch(definition.name) is None
    ):
        raise ValueError("tool name is invalid")
    if (
        type(definition.description) is not str
        or not definition.description.strip()
        or len(definition.description) > 1_000
    ):
        raise ValueError("tool description is invalid")
    schema = definition.input_schema
    if type(schema) is not dict or schema.get("type") != "object":
        raise ValueError("tool input schema must describe an object")
    try:
        json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError("tool input schema is not JSON serializable") from None
    if not callable(tool.validate) or not callable(tool.handler):
        raise TypeError("tool validator and handler must be callable")
    if tool.activity_kind not in {"tool", "command", "control"}:
        raise ValueError("tool activity kind is invalid")
    if not isinstance(tool.effect, ToolEffect):
        raise TypeError("tool effect is invalid")
    if type(tool.parallel_safe) is not bool:
        raise TypeError("tool parallel_safe flag must be boolean")
    if tool.parallel_safe and tool.effect is not ToolEffect.READ_ONLY:
        raise ValueError("only read-only tools can be parallel-safe")


def _failure(call: ToolCall, error_code: str, error_message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        ok=False,
        output="",
        error_code=error_code,
        error_message=error_message,
    )

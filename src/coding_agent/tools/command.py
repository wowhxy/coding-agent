"""Bounded local command execution for one configured workspace."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..context import truncate_text
from ..protocol import ToolDefinition, ToolResult
from .registry import RegisteredTool, ToolArgumentError, ToolEffect, require_keys


MAX_COMMAND_OUTPUT_CHARS = 20_000
SENSITIVE_ENV_NAMES_VARIABLE = "CODING_AGENT_SENSITIVE_ENV_NAMES"


def create_execute_command_tool(
    workspace_root: str | Path,
    sensitive_env_names: Iterable[str],
    default_timeout: int = 30,
    max_timeout: int = 120,
) -> RegisteredTool:
    """Create a synchronous shell command tool fixed to one workspace root."""

    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace_root must be a directory")
    if not _is_positive_int(max_timeout):
        raise ValueError("max_timeout must be a positive integer")
    if not _is_positive_int(default_timeout) or default_timeout > max_timeout:
        raise ValueError(
            "default_timeout must be a positive integer no greater than max_timeout"
        )

    configured_sensitive_names = _normalize_sensitive_names(
        sensitive_env_names
    )
    definition = ToolDefinition(
        name="execute_command",
        description=(
            "Run a shell command in the workspace and return its exit code, "
            "stdout, and stderr."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": max_timeout,
                    "default": default_timeout,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(
            arguments,
            required={"command"},
            optional={"timeout_seconds"},
        )
        command = arguments["command"]
        timeout_seconds = arguments.get("timeout_seconds", default_timeout)
        if not isinstance(command, str):
            raise ToolArgumentError("command must be a string")
        if command.strip() == "":
            raise ToolArgumentError("command must not be empty")
        if (
            not _is_positive_int(timeout_seconds)
            or timeout_seconds > max_timeout
        ):
            raise ToolArgumentError(
                f"timeout_seconds must be an integer from 1 to {max_timeout}"
            )
        return {
            "command": command,
            "timeout_seconds": timeout_seconds,
        }

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        timeout_seconds = arguments["timeout_seconds"]
        child_env = os.environ.copy()
        dynamic_sensitive_names = _normalize_sensitive_names(
            os.environ.get(SENSITIVE_ENV_NAMES_VARIABLE, "").split(",")
        )
        for name in configured_sensitive_names | dynamic_sensitive_names:
            child_env.pop(name, None)

        try:
            completed = subprocess.run(
                arguments["command"],
                shell=True,
                cwd=root,
                env=child_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            output = _format_output(
                "timeout",
                _normalize_process_output(exc.stdout),
                _normalize_process_output(exc.stderr),
            )
            return ToolResult(
                call_id,
                "execute_command",
                False,
                truncate_text(output, MAX_COMMAND_OUTPUT_CHARS),
                "COMMAND_TIMEOUT",
                f"command timed out after {timeout_seconds} seconds",
            )

        output = _format_output(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
        output = truncate_text(output, MAX_COMMAND_OUTPUT_CHARS)
        if completed.returncode == 0:
            return ToolResult(call_id, "execute_command", True, output)
        return ToolResult(
            call_id,
            "execute_command",
            False,
            output,
            "COMMAND_FAILED",
            f"command exited with status {completed.returncode}",
        )

    return RegisteredTool(
        definition,
        validate,
        handle,
        activity_kind="command",
        effect=ToolEffect.MUTATING,
        parallel_safe=False,
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _normalize_sensitive_names(names: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            raise ValueError("sensitive environment variable names must be strings")
        stripped = name.strip()
        if stripped:
            normalized.add(stripped)
    return frozenset(normalized)


def _normalize_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _format_output(exit_code: int | str, stdout: str, stderr: str) -> str:
    return (
        f"exit_code: {exit_code}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )

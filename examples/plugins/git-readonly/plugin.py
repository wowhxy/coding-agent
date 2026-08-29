"""Trusted example plugin providing three constrained, read-only Git tools."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from coding_agent.plugins import PluginContext
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, ToolArgumentError, require_keys


_COMMAND_TIMEOUT_SECONDS = 10
_MAX_OUTPUT_CHARS = 20_000
_MAX_PATH_CHARS = 500
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:API[_-]?KEY|TOKEN|PASSWORD|PASSWD|SECRET|CREDENTIAL|AUTHORIZATION)",
    re.IGNORECASE,
)


def get_tools(context: PluginContext) -> tuple[RegisteredTool, ...]:
    """Return this plugin's fixed tool set for one workspace runtime."""

    workspace = Path(context.workspace)
    return (
        RegisteredTool(
            ToolDefinition(
                name="git_status",
                description="Show concise Git working-tree status without modifying it.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            _validate_status,
            lambda call_id, _arguments: _run_git(
                call_id, "git_status", workspace, ["status", "--short"]
            ),
        ),
        RegisteredTool(
            ToolDefinition(
                name="git_diff",
                description=(
                    "Show an unstaged or staged Git diff, optionally for one safe "
                    "workspace-relative path."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "staged": {"type": "boolean"},
                        "path": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            _validate_diff,
            lambda call_id, arguments: _run_git(
                call_id,
                "git_diff",
                workspace,
                _diff_arguments(arguments),
            ),
        ),
        RegisteredTool(
            ToolDefinition(
                name="git_log",
                description="Show a bounded one-line Git commit history.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "max_count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            _validate_log,
            lambda call_id, arguments: _run_git(
                call_id,
                "git_log",
                workspace,
                ["log", "--oneline", f"--max-count={arguments['max_count']}"],
            ),
        ),
    )


def _validate_status(arguments: dict[str, Any]) -> dict[str, Any]:
    require_keys(arguments, ())
    return {}


def _validate_diff(arguments: dict[str, Any]) -> dict[str, Any]:
    require_keys(arguments, (), ("staged", "path"))
    staged = arguments.get("staged", False)
    if type(staged) is not bool:
        raise ToolArgumentError("staged must be a boolean")
    normalized: dict[str, Any] = {"staged": staged}
    if "path" in arguments:
        normalized["path"] = _safe_relative_path(arguments["path"])
    return normalized


def _validate_log(arguments: dict[str, Any]) -> dict[str, Any]:
    require_keys(arguments, (), ("max_count",))
    count = arguments.get("max_count", 10)
    if type(count) is not int or not 1 <= count <= 20:
        raise ToolArgumentError("max_count must be an integer from 1 to 20")
    return {"max_count": count}


def _safe_relative_path(value: Any) -> str:
    if type(value) is not str:
        raise ToolArgumentError("path must be a string")
    path = value.strip()
    if not path or len(path) > _MAX_PATH_CHARS or "\x00" in path:
        raise ToolArgumentError("path must be a non-empty relative path")
    windows_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path.replace("\\", "/"))
    if (
        windows_path.is_absolute()
        or windows_path.drive
        or posix_path.is_absolute()
        or any(part == ".." for part in windows_path.parts)
        or any(part == ".." for part in posix_path.parts)
        or posix_path.as_posix() == "."
    ):
        raise ToolArgumentError("path must stay within the workspace")
    return posix_path.as_posix()


def _diff_arguments(arguments: dict[str, Any]) -> list[str]:
    command = ["diff", "--no-ext-diff", "--no-textconv"]
    if arguments["staged"]:
        command.append("--cached")
    if "path" in arguments:
        command.extend(("--", arguments["path"]))
    return command


def _run_git(
    call_id: str,
    tool_name: str,
    workspace: Path,
    arguments: list[str],
) -> ToolResult:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            env=_filtered_environment(),
        )
    except FileNotFoundError:
        return _failure(call_id, tool_name, "GIT_UNAVAILABLE", "Git is unavailable")
    except subprocess.TimeoutExpired as exc:
        output = _combined_output(exc.output, exc.stderr)
        return _failure(
            call_id,
            tool_name,
            "GIT_TIMEOUT",
            "Git command timed out",
            output,
        )
    except OSError:
        return _failure(
            call_id,
            tool_name,
            "GIT_COMMAND_FAILED",
            "Git command could not be started",
        )

    output = _combined_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        return _failure(
            call_id,
            tool_name,
            "GIT_COMMAND_FAILED",
            f"Git command exited with code {completed.returncode}",
            output,
        )
    return ToolResult(call_id, tool_name, True, output=_bounded(output))


def _filtered_environment() -> dict[str, str]:
    configured_names = {
        name.strip()
        for name in os.environ.get(
            "CODING_AGENT_SENSITIVE_ENV_NAMES", ""
        ).split(",")
        if name.strip()
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if not _SENSITIVE_ENV_NAME.search(name)
        and name not in configured_names
        and name != "CODING_AGENT_SENSITIVE_ENV_NAMES"
        and not name.upper().startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_PAGER"] = "cat"
    return environment


def _combined_output(stdout: Any, stderr: Any) -> str:
    parts = [_as_text(stdout).rstrip(), _as_text(stderr).rstrip()]
    return _bounded("\n".join(part for part in parts if part))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _bounded(value: str) -> str:
    if len(value) <= _MAX_OUTPUT_CHARS:
        return value
    marker = "\n...[Git output truncated]...\n"
    remaining = _MAX_OUTPUT_CHARS - len(marker)
    head = remaining // 2
    return value[:head] + marker + value[-(remaining - head) :]


def _failure(
    call_id: str,
    tool_name: str,
    code: str,
    message: str,
    output: str = "",
) -> ToolResult:
    return ToolResult(
        call_id,
        tool_name,
        False,
        output=_bounded(output),
        error_code=code,
        error_message=message,
    )

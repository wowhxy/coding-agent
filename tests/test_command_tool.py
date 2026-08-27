from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent.context import truncate_text
from coding_agent.protocol import ToolCall, ToolResult
from coding_agent.tools.command import create_execute_command_tool
from coding_agent.tools.registry import ToolRegistry


def python_command(code: str) -> str:
    """Build one shell-safe command using the current Python interpreter."""

    parts = [sys.executable, "-c", code]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _dispatch(
    root: Path,
    arguments: dict[str, object],
    *,
    sensitive_env_names: tuple[str, ...] = (),
) -> ToolResult:
    registry = ToolRegistry()
    registry.register(
        create_execute_command_tool(
            root,
            sensitive_env_names=sensitive_env_names,
        )
    )
    return registry.dispatch(
        ToolCall("command-1", "execute_command", json.dumps(arguments))
    )


def _stdout(output: str) -> str:
    return output.split("stdout:\n", 1)[1].split("\nstderr:\n", 1)[0]


def test_execute_command_returns_zero_exit_and_structured_output(
    tmp_path: Path,
) -> None:
    result = _dispatch(
        tmp_path,
        {"command": python_command("print('hello')")},
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.error_message is None
    assert result.output.startswith("exit_code: 0\nstdout:\nhello")
    assert "\nstderr:\n" in result.output


def test_execute_command_preserves_output_for_nonzero_exit(
    tmp_path: Path,
) -> None:
    command = python_command(
        "import sys; print('ordinary output'); "
        "print('failure detail', file=sys.stderr); raise SystemExit(7)"
    )

    result = _dispatch(tmp_path, {"command": command})

    assert result.ok is False
    assert result.error_code == "COMMAND_FAILED"
    assert result.error_message is not None
    assert "7" in result.error_message
    assert result.output.startswith("exit_code: 7\n")
    assert "ordinary output" in result.output
    assert "failure detail" in result.output


def test_execute_command_uses_workspace_as_cwd(tmp_path: Path) -> None:
    result = _dispatch(
        tmp_path,
        {"command": python_command("import os; print(os.getcwd())")},
    )

    assert result.ok is True
    assert Path(_stdout(result.output).strip()).resolve() == tmp_path.resolve()


def test_execute_command_inherits_ordinary_env_and_filters_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISIBLE_TEST_ENV", "ordinary-value")
    monkeypatch.setenv("PROVIDER_TEST_KEY", "provider-secret-value")
    monkeypatch.setenv("EXTRA_SECRET_A", "extra-secret-a-value")
    monkeypatch.setenv("EXTRA_SECRET_B", "extra-secret-b-value")
    monkeypatch.setenv(
        "CODING_AGENT_SENSITIVE_ENV_NAMES",
        " EXTRA_SECRET_A,EXTRA_SECRET_B ,, ",
    )
    code = (
        "import json, os; print(json.dumps({"
        "'visible': os.getenv('VISIBLE_TEST_ENV'), "
        "'provider': os.getenv('PROVIDER_TEST_KEY'), "
        "'extra_a': os.getenv('EXTRA_SECRET_A'), "
        "'extra_b': os.getenv('EXTRA_SECRET_B')}))"
    )

    result = _dispatch(
        tmp_path,
        {"command": python_command(code)},
        sensitive_env_names=("PROVIDER_TEST_KEY",),
    )

    assert result.ok is True
    observed = json.loads(_stdout(result.output))
    assert observed == {
        "visible": "ordinary-value",
        "provider": None,
        "extra_a": None,
        "extra_b": None,
    }


@pytest.mark.parametrize("timeout_seconds", [0, 121, True, 1.5])
def test_execute_command_rejects_invalid_timeout(
    tmp_path: Path,
    timeout_seconds: object,
) -> None:
    result = _dispatch(
        tmp_path,
        {
            "command": python_command("print('must not run')"),
            "timeout_seconds": timeout_seconds,
        },
    )

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message == "timeout_seconds must be an integer from 1 to 120"


def test_execute_command_rejects_unknown_arguments(tmp_path: Path) -> None:
    result = _dispatch(
        tmp_path,
        {"command": python_command("print('must not run')"), "cwd": ".."},
    )

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message == "unknown field: cwd"


def test_execute_command_times_out_with_available_partial_output(
    tmp_path: Path,
) -> None:
    command = python_command(
        "import time; print('started', flush=True); time.sleep(5)"
    )

    result = _dispatch(
        tmp_path,
        {"command": command, "timeout_seconds": 1},
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_TIMEOUT"
    assert result.error_message is not None
    assert "1" in result.error_message
    assert result.output.startswith("exit_code: timeout\nstdout:\n")
    assert "\nstderr:\n" in result.output
    partial_stdout = _stdout(result.output).strip()
    assert partial_stdout in {"", "started"}


def test_execute_command_truncates_large_output_deterministically(
    tmp_path: Path,
) -> None:
    command = python_command(
        "import sys; print('A' * 21000); print('Z' * 21000, file=sys.stderr)"
    )

    result = _dispatch(tmp_path, {"command": command})

    assert result.ok is True
    assert len(result.output) == 20_000
    assert result.output == truncate_text(result.output[:0] + _untruncated_output(), 20_000)
    assert result.output.startswith("exit_code: 0\nstdout:\n")
    assert "[output truncated: original=" in result.output
    assert "Z" * 100 in result.output[-200:]


def _untruncated_output() -> str:
    stdout = "A" * 21_000 + "\n"
    stderr = "Z" * 21_000 + "\n"
    return f"exit_code: 0\nstdout:\n{stdout}\nstderr:\n{stderr}"

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from coding_agent.plugins import PluginManager
from coding_agent.protocol import ToolCall
from coding_agent.tool_execution import ToolExecutionScheduler
from coding_agent.tools.registry import ToolEffect, ToolRegistry


EXAMPLE = Path("examples/plugins/git-readonly")


def _installed_manager(tmp_path: Path) -> tuple[PluginManager, Path]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copytree(EXAMPLE, home / "plugins" / "git-readonly")
    manager = PluginManager(home, workspace, ToolRegistry())
    manager.enable("git-readonly", persist=False)
    return manager, workspace


def _dispatch(manager: PluginManager, name: str, arguments: object):
    return manager.registry.dispatch(
        ToolCall("call-1", name, json.dumps(arguments))
    )


def test_git_status_uses_fixed_argv_workspace_and_filtered_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace = _installed_manager(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-provider-secret")
    monkeypatch.setenv("CUSTOM_PROVIDER_AUTH", "synthetic-custom-secret")
    monkeypatch.setenv(
        "CODING_AGENT_SENSITIVE_ENV_NAMES", "CUSTOM_PROVIDER_AUTH"
    )
    monkeypatch.setenv("SAFE_DEMO_VALUE", "kept")

    def fake_run(argv: list[str], **kwargs: object):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, " M calculator.py\n", "warning\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _dispatch(manager, "git_status", {})

    assert result.ok is True
    assert "calculator.py" in result.output
    assert "warning" in result.output
    assert calls[0][0] == ["git", "status", "--short"]
    assert calls[0][1]["cwd"] == workspace
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 10
    assert calls[0][1]["check"] is False
    environment = calls[0][1]["env"]
    assert isinstance(environment, dict)
    assert "DEEPSEEK_API_KEY" not in environment
    assert "CUSTOM_PROVIDER_AUTH" not in environment
    assert "CODING_AGENT_SENSITIVE_ENV_NAMES" not in environment
    assert environment["SAFE_DEMO_VALUE"] == "kept"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_PAGER"] == "cat"


@pytest.mark.parametrize(
    ("arguments", "expected_argv"),
    [
        ({}, ["git", "diff", "--no-ext-diff", "--no-textconv"]),
        ({"staged": False}, ["git", "diff", "--no-ext-diff", "--no-textconv"]),
        ({"staged": True}, ["git", "diff", "--no-ext-diff", "--no-textconv", "--cached"]),
        (
            {"staged": True, "path": "src/calculator.py"},
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--cached",
                "--",
                "src/calculator.py",
            ],
        ),
    ],
)
def test_git_diff_accepts_only_constrained_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
    expected_argv: list[str],
) -> None:
    manager, _workspace = _installed_manager(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "diff output", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _dispatch(manager, "git_diff", arguments).ok is True
    assert calls == [expected_argv]


@pytest.mark.parametrize(
    "arguments",
    [
        {"staged": "yes"},
        {"path": "../outside.py"},
        {"path": ""},
        {"path": "C:/outside.py"},
        {"args": ["--no-index", "a", "b"]},
    ],
)
def test_git_diff_rejects_injection_and_unsafe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
) -> None:
    manager, _workspace = _installed_manager(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not run"),
    )

    result = _dispatch(manager, "git_diff", arguments)

    assert result.error_code == "MALFORMED_ARGUMENTS"


@pytest.mark.parametrize("max_count", [1, 20])
def test_git_log_bounds_count_and_uses_fixed_oneline_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_count: int,
) -> None:
    manager, _workspace = _installed_manager(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "abc123 commit", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _dispatch(manager, "git_log", {"max_count": max_count})

    assert result.ok is True
    assert calls == [["git", "log", "--oneline", f"--max-count={max_count}"]]


@pytest.mark.parametrize("value", [0, 21, True, "10", 1.5])
def test_git_log_rejects_invalid_count_without_running_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: object,
) -> None:
    manager, _workspace = _installed_manager(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not run"),
    )

    assert _dispatch(
        manager, "git_log", {"max_count": value}
    ).error_code == "MALFORMED_ARGUMENTS"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (subprocess.CompletedProcess(["git"], 128, "", "not a git repository"), "GIT_COMMAND_FAILED"),
        (FileNotFoundError(), "GIT_UNAVAILABLE"),
        (subprocess.TimeoutExpired(["git"], 10, output="partial", stderr="late"), "GIT_TIMEOUT"),
    ],
)
def test_git_failures_return_structured_bounded_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: object,
    expected_code: str,
) -> None:
    manager, _workspace = _installed_manager(tmp_path)

    def fake_run(*_args: object, **_kwargs: object):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _dispatch(manager, "git_status", {})

    assert result.ok is False
    assert result.error_code == expected_code
    assert result.error_message
    assert len(result.output) <= 20_000


def test_plugin_exposes_only_three_read_only_git_tools(tmp_path: Path) -> None:
    manager, _workspace = _installed_manager(tmp_path)

    assert [item.name for item in manager.registry.definitions()] == [
        "git_status",
        "git_diff",
        "git_log",
    ]
    assert all(
        manager.registry.execution_metadata_for(name)
        == (ToolEffect.READ_ONLY, True)
        for name in ("git_status", "git_diff", "git_log")
    )
    assert manager.registry.dispatch(
        ToolCall("x", "git_commit", "{}")
    ).error_code == "UNKNOWN_TOOL"


def test_explicit_git_plugin_tools_execute_in_one_parallel_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches Plugin metadata being ignored by the scheduler."""

    manager, _workspace = _installed_manager(tmp_path)
    entered = threading.Barrier(4)
    release = threading.Event()

    def fake_run(argv: list[str], **_kwargs: object):
        entered.wait(timeout=2)
        assert release.wait(timeout=2)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    def release_all() -> None:
        entered.wait(timeout=2)
        release.set()

    coordinator = threading.Thread(target=release_all)
    coordinator.start()
    outcome = ToolExecutionScheduler(manager.registry).execute(
        (
            ToolCall("status", "git_status", "{}"),
            ToolCall("diff", "git_diff", "{}"),
            ToolCall("log", "git_log", "{}"),
        )
    )
    coordinator.join(timeout=2)

    assert not coordinator.is_alive()
    assert tuple(result.tool_call_id for result in outcome.results) == (
        "status",
        "diff",
        "log",
    )
    assert all(result.ok for result in outcome.results)
    assert outcome.stats.parallel_groups == 1
    assert outcome.stats.parallel_calls == 3
    assert all(
        manager.registry.source_of(name) == "plugin:git-readonly"
        for name in ("git_status", "git_diff", "git_log")
    )


def test_real_temporary_repository_status_diff_and_log_are_read_only(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable")
    manager, workspace = _installed_manager(tmp_path)
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "plugin-test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Plugin Test"],
        cwd=workspace,
        check=True,
    )
    calculator = workspace / "calculator.py"
    calculator.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calculator.py"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    calculator.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout
    before_status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout

    status = _dispatch(manager, "git_status", {})
    diff = _dispatch(manager, "git_diff", {"path": "calculator.py"})
    log = _dispatch(manager, "git_log", {"max_count": 5})

    assert status.ok and "calculator.py" in status.output
    assert diff.ok and "return a - b" in diff.output
    assert log.ok and "initial" in log.output
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout == before_head
    assert subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout == before_status

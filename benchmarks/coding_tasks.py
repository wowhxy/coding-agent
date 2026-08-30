"""Disposable deterministic coding tasks for benchmark category J."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager
from coding_agent.protocol import ModelTurn, RunStatus, ToolCall
from coding_agent.subagents.control import create_delegate_tasks_tool
from coding_agent.subagents.manager import SubagentManager
from coding_agent.tools import build_default_registry

from .fakes import FakeModelClient


def run_coding_tasks(root: Path) -> dict[str, object]:
    """Run eight bounded tasks against real AgentRunner and local tools."""

    root.mkdir()
    tasks = [
        _simple_python_bugfix(root / "simple-python"),
        _multi_file_python_bugfix(root / "multi-file-python"),
        _add_tests(root / "add-tests"),
        _read_only_code_review(root / "code-review"),
        _create_project(root / "create-project"),
        _cpp_compile_test(root / "cpp"),
        _repository_exploration(root / "exploration"),
        _subagent_investigation(root / "subagents"),
    ]
    return {
        "tasks": tasks,
        "completed": sum(item["status"] == "passed" for item in tasks),
        "skipped": sum(item["status"] == "skipped" for item in tasks),
        "model_calls": sum(int(item["model_calls"]) for item in tasks),
        "tool_calls": sum(int(item["tool_calls"]) for item in tasks),
        "subagent_calls": sum(int(item["subagent_calls"]) for item in tasks),
    }


def _simple_python_bugfix(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    (workspace / "duration.py").write_text(
        "def clamp_percentage(value: int) -> int:\n"
        "    return min(100, value)\n",
        encoding="utf-8",
    )
    (workspace / "test_duration.py").write_text(
        "from duration import clamp_percentage\n\n"
        "def test_below_range():\n"
        "    assert clamp_percentage(-5) == 0\n",
        encoding="utf-8",
    )
    script = [
        _calls(_call("list", "list_files", {"path": "."})),
        _calls(_call("read", "read_file", {"path": "duration.py"})),
        _calls(_call("test-before", "execute_command", {"command": _pytest_command()})),
        _calls(
            _call(
                "edit",
                "replace_in_file",
                {
                    "path": "duration.py",
                    "old_text": "return min(100, value)",
                    "new_text": "return min(100, max(0, value))",
                },
            )
        ),
        _calls(_call("test-after", "execute_command", {"command": _pytest_command()})),
        ModelTurn("fixed and verified"),
    ]
    return _run_task(
        "simple_python_bugfix",
        workspace,
        script,
        lambda: _pytest_passes(workspace),
    )


def _multi_file_python_bugfix(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    (workspace / "normalize.py").write_text(
        "def normalized_name(value: str) -> str:\n"
        "    return value.strip()\n",
        encoding="utf-8",
    )
    (workspace / "service.py").write_text(
        "from normalize import normalized_name\n\n"
        "def same_user(left: str, right: str) -> bool:\n"
        "    return normalized_name(left) == normalized_name(right)\n",
        encoding="utf-8",
    )
    (workspace / "test_service.py").write_text(
        "from service import same_user\n\n"
        "def test_names_are_case_insensitive():\n"
        "    assert same_user(' Alice ', 'alice')\n",
        encoding="utf-8",
    )
    script = [
        _calls(_call("list", "list_files", {"path": "."})),
        _calls(
            _call("read-normalize", "read_file", {"path": "normalize.py"}),
            _call("read-service", "read_file", {"path": "service.py"}),
        ),
        _calls(_call("test-before", "execute_command", {"command": _pytest_command()})),
        _calls(
            _call(
                "edit",
                "replace_in_file",
                {
                    "path": "normalize.py",
                    "old_text": "return value.strip()",
                    "new_text": "return value.strip().casefold()",
                },
            )
        ),
        _calls(_call("test-after", "execute_command", {"command": _pytest_command()})),
        ModelTurn("multi-file behavior fixed and verified"),
    ]
    return _run_task(
        "multi_file_python_bugfix",
        workspace,
        script,
        lambda: _pytest_passes(workspace),
    )


def _add_tests(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    (workspace / "slug.py").write_text(
        "def slugify(value: str) -> str:\n"
        "    return '-'.join(value.strip().lower().split())\n",
        encoding="utf-8",
    )
    content = (
        "from slug import slugify\n\n"
        "def test_slugify_collapses_whitespace():\n"
        "    assert slugify('  Hello   World  ') == 'hello-world'\n"
    )
    script = [
        _calls(_call("read", "read_file", {"path": "slug.py"})),
        _calls(
            _call(
                "write-test",
                "write_file",
                {"path": "test_slug.py", "content": content},
            )
        ),
        _calls(_call("test", "execute_command", {"command": _pytest_command()})),
        ModelTurn("tests added and verified"),
    ]
    return _run_task(
        "add_tests",
        workspace,
        script,
        lambda: _pytest_passes(workspace) and (workspace / "test_slug.py").exists(),
    )


def _read_only_code_review(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    (workspace / "security.py").write_text(
        "def is_allowed(path: str) -> bool:\n"
        "    return '..' not in path\n",
        encoding="utf-8",
    )
    before = _tree_digest(workspace)
    script = [
        _calls(_call("list", "list_files", {"path": "."})),
        _calls(_call("read", "read_file", {"path": "security.py"})),
        ModelTurn("Review: string matching alone is not a canonical path boundary."),
    ]
    return _run_task(
        "read_only_code_review",
        workspace,
        script,
        lambda: _tree_digest(workspace) == before,
    )


def _create_project(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    module = "def add(left: int, right: int) -> int:\n    return left + right\n"
    tests = (
        "from calculator import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    script = [
        _calls(
            _call("write-module", "write_file", {"path": "calculator.py", "content": module}),
            _call("write-tests", "write_file", {"path": "test_calculator.py", "content": tests}),
        ),
        _calls(_call("test", "execute_command", {"command": _pytest_command()})),
        ModelTurn("project created and verified"),
    ]
    return _run_task(
        "create_project",
        workspace,
        script,
        lambda: _pytest_passes(workspace),
    )


def _cpp_compile_test(workspace: Path) -> dict[str, object]:
    compiler = shutil.which("g++")
    if compiler is None:
        return _skipped("cpp_compile_test", "g++ unavailable")
    workspace.mkdir()
    (workspace / "main.cpp").write_text(
        "#include <iostream>\n"
        "int main() { std::cout << (2 - 3) << '\\n'; }\n",
        encoding="utf-8",
    )
    binary = "benchmark_app.exe" if os.name == "nt" else "benchmark_app"
    compiler_command = subprocess.list2cmdline(
        [compiler, "-std=c++17", "main.cpp", "-o", binary]
    )
    run_binary = binary if os.name == "nt" else f"./{binary}"
    command = f"{compiler_command} && {run_binary}"
    script = [
        _calls(_call("read", "read_file", {"path": "main.cpp"})),
        _calls(
            _call(
                "edit",
                "replace_in_file",
                {
                    "path": "main.cpp",
                    "old_text": "(2 - 3)",
                    "new_text": "(2 + 3)",
                },
            )
        ),
        _calls(_call("compile", "execute_command", {"command": command})),
        ModelTurn("C++ task compiled and verified"),
    ]

    def verify() -> bool:
        completed = subprocess.run(
            [str(workspace / binary)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "5"

    return _run_task("cpp_compile_test", workspace, script, verify)


def _repository_exploration(workspace: Path) -> dict[str, object]:
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "parser.py").write_text(
        "def parse_unicode(value: str) -> str:\n    return value.casefold()\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("Unicode parser project\n", encoding="utf-8")
    before = _tree_digest(workspace)
    script = [
        _calls(_call("list", "list_files", {"path": "."})),
        _calls(_call("search", "search_text", {"path": ".", "query": "parse_unicode"})),
        _calls(_call("read", "read_file", {"path": "src/parser.py"})),
        ModelTurn("The Unicode parser is implemented in src/parser.py."),
    ]
    return _run_task(
        "repository_exploration",
        workspace,
        script,
        lambda: _tree_digest(workspace) == before,
    )


def _subagent_investigation(workspace: Path) -> dict[str, object]:
    workspace.mkdir()
    for name in ("parser.py", "config.py", "tests.txt"):
        (workspace / name).write_text(f"evidence in {name}\n", encoding="utf-8")
    child_clients: list[FakeModelClient] = []

    def child_factory() -> FakeModelClient:
        client = FakeModelClient(
            [
                _calls(_call("child-list", "list_files", {"path": "."})),
                ModelTurn("bounded child finding"),
            ]
        )
        child_clients.append(client)
        return client

    manager = SubagentManager(workspace, child_factory)
    registry = build_default_registry(_config(workspace))
    registry.register_many(
        (create_delegate_tasks_tool(manager),), source="control:subagent"
    )
    delegation = {
        "tasks": [
            {"task": "inspect parser", "role": "explore"},
            {"task": "inspect config", "role": "analysis"},
            {"task": "review tests", "role": "review"},
        ]
    }
    parent = FakeModelClient(
        [
            _calls(_call("delegate", "delegate_tasks", delegation)),
            ModelTurn("parallel investigation completed"),
        ]
    )
    runner = AgentRunner(
        parent,
        registry,
        ContextManager(),
        run_start_hook=manager.begin_parent_run,
        context_snapshot_sink=manager.observe_parent_context,
    )
    started = time.perf_counter()
    result = runner.run("system", "investigate independent project areas")
    elapsed = time.perf_counter() - started
    passed = (
        result.status is RunStatus.FINAL_RESPONSE
        and len(child_clients) == 3
        and all(len(client.calls) == 2 and client.closed for client in child_clients)
    )
    return {
        "name": "subagent_investigation",
        "status": "passed" if passed else "failed",
        "verification_passed": passed,
        "elapsed_seconds": round(elapsed, 6),
        "protocol_status": result.status.value,
        "agent_steps": result.steps,
        "model_calls": len(parent.calls),
        "tool_calls": runner.last_tool_execution_stats.tool_calls_total,
        "subagent_calls": len(child_clients),
    }


def _run_task(
    name: str,
    workspace: Path,
    script: list[ModelTurn],
    verify,
) -> dict[str, object]:
    client = FakeModelClient(script)
    runner = AgentRunner(
        client,
        build_default_registry(_config(workspace)),
        ContextManager(),
        max_steps=20,
    )
    started = time.perf_counter()
    result = runner.run("system", f"benchmark task: {name}")
    elapsed = time.perf_counter() - started
    verification_passed = bool(verify())
    passed = result.status is RunStatus.FINAL_RESPONSE and verification_passed
    return {
        "name": name,
        "status": "passed" if passed else "failed",
        "verification_passed": verification_passed,
        "elapsed_seconds": round(elapsed, 6),
        "protocol_status": result.status.value,
        "agent_steps": result.steps,
        "model_calls": len(client.calls),
        "tool_calls": runner.last_tool_execution_stats.tool_calls_total,
        "subagent_calls": 0,
    }


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace.resolve(),
        "https://example.test/v1",
        "benchmark-fake",
        "offline-benchmark-secret",
        "BENCHMARK_API_KEY",
        "disabled",
        frozenset({"BENCHMARK_API_KEY"}),
        20,
        80_000,
        8,
        20_000,
        30,
    )


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False))


def _calls(*calls: ToolCall) -> ModelTurn:
    return ModelTurn(tool_calls=tuple(calls))


def _pytest_command() -> str:
    return subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q"])


def _pytest_passes(workspace: Path) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return completed.returncode == 0


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _skipped(name: str, reason: str) -> dict[str, object]:
    return {
        "name": name,
        "status": "skipped",
        "verification_passed": None,
        "skip_reason": reason,
        "elapsed_seconds": 0.0,
        "protocol_status": "SKIPPED",
        "agent_steps": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "subagent_calls": 0,
    }

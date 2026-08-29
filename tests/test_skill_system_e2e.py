from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import resolve_config
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, ModelTurn, Role, RunStatus, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.skill_selector import SkillActivator, SkillSelector
from coding_agent.skills import SkillRegistry
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)


def _python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_automatic_cmake_skill_drives_real_local_inspect_edit_verify_loop(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    cmake = workspace / "CMakeLists.txt"
    cmake.write_text(
        "cmake_minimum_required(VERSION 3.20)\n"
        "project(skill_demo LANGUAGES CXX)\n"
        "set(APP_MODE broken)\n",
        encoding="utf-8",
    )
    (workspace / "verify_build.py").write_text(
        "from pathlib import Path\n"
        "text = Path('CMakeLists.txt').read_text(encoding='utf-8')\n"
        "assert 'set(APP_MODE fixed)' in text\n"
        "print('cmake verification passed')\n",
        encoding="utf-8",
    )
    _write_skill(
        workspace / ".coding-agent" / "skills",
        "cpp-cmake",
        "Use for C++ projects built with CMake.",
        "Inspect CMakeLists.txt, reproduce the failure, make a minimal edit, and rerun verification.",
    )
    verify = _python_command("-B", "verify_build.py")
    client = FakeModelClient(
        [
            ModelTurn('{"skills":["cpp-cmake"]}'),
            ModelTurn(tool_calls=(ToolCall("list", "list_files", '{"path":"."}'),)),
            ModelTurn(tool_calls=(ToolCall("read", "read_file", '{"path":"CMakeLists.txt"}'),)),
            ModelTurn(
                tool_calls=(
                    ToolCall("before", "execute_command", json.dumps({"command": verify})),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "replace_in_file",
                        json.dumps(
                            {
                                "path": "CMakeLists.txt",
                                "old_text": "set(APP_MODE broken)",
                                "new_text": "set(APP_MODE fixed)",
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall("after", "execute_command", json.dumps({"command": verify})),
                )
            ),
            ModelTurn("Fixed the CMake configuration and verified it."),
        ]
    )
    registry = SkillRegistry(home, workspace)
    registry.discover()
    activation = SkillActivator(registry, SkillSelector(client)).prepare(
        "Repair the failing CMake project."
    )
    config = resolve_config(
        workspace=workspace,
        base_url="https://example.test/v1",
        model="test-model",
        environ={"OPENAI_API_KEY": "fake-e2e-key"},
    )
    runner = AgentRunner(client, build_default_registry(config), ContextManager())
    runner.set_active_skills(activation.skills)
    history = ConversationHistory(SYSTEM_PROMPT)

    result = runner.run_turn(history, "Repair the failing CMake project.")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.steps == 6
    assert "set(APP_MODE fixed)" in cmake.read_text(encoding="utf-8")
    assert sum(not definitions for _messages, definitions in client.calls) == 1
    first_agent_request = client.calls[1][0]
    assert "cpp-cmake" in (first_agent_request[1].content or "")
    assert "CMakeLists.txt" in (first_agent_request[1].content or "")
    command_feedback = {
        message.tool_call_id: json.loads(message.content or "{}")
        for message in client.calls[-1][0]
        if message.role is Role.TOOL
    }
    assert command_feedback["before"]["ok"] is False
    assert command_feedback["after"]["ok"] is True
    assert all('{"skills"' not in (item.content or "") for item in history.messages)


def test_manual_use_and_off_change_only_future_turn_skill_context(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(home / "skills", "manual", "Manual workflow.", "manual body")
    _write_skill(home / "skills", "other", "Other workflow.", "other body")
    registry = SkillRegistry(home, workspace)
    registry.discover()
    model = FakeModelClient(
        [
            ModelTurn('{"skills":[]}'),
            ModelTurn("first done"),
            ModelTurn('{"skills":[]}'),
            ModelTurn("second done"),
        ]
    )
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    store = JsonSessionStore(
        tmp_path / "sessions", clock=lambda: NOW, id_generator=lambda: "111111111111"
    )
    session = InteractiveSession(
        runner,
        ConversationHistory("core"),
        store.create_session(workspace, "p", "m"),
        store,
        "p",
        "m",
        (),
    )
    commands = iter(
        ("/skill use manual", "first task", "/skill off manual", "second task", "/exit")
    )
    shell = InteractiveShell(
        session,
        store,
        lambda _prompt: next(commands),
        lambda _line: None,
        skill_registry=registry,
        skill_activator=SkillActivator(registry, SkillSelector(model)),
    )

    assert shell.run() == 0

    assert "manual body" in "\n".join(
        item.content or "" for item in model.calls[1][0]
    )
    assert all(
        "manual body" not in (item.content or "") for item in model.calls[3][0]
    )


def test_workspace_precedence_and_selector_failure_recover_offline(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(home / "skills", "method", "User method.", "user body")
    _write_skill(
        workspace / ".coding-agent" / "skills",
        "method",
        "Workspace method.",
        "workspace body",
    )
    registry = SkillRegistry(home, workspace)
    assert registry.discover()[0].scope == "workspace"
    assert registry.load("method").body == "workspace body"
    model = FakeModelClient([ModelTurn("malformed"), ModelTurn("task still completed")])
    activation = SkillActivator(registry, SkillSelector(model)).prepare("task")
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    runner.set_active_skills(activation.skills)

    result = runner.run("core", "task")

    assert activation.skills == ()
    assert [item.code for item in activation.diagnostics] == [
        "SKILL_SELECTOR_FAILED"
    ]
    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "task still completed"

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

from coding_agent.application.service import CodingAgentService
from coding_agent.config import RuntimeConfig
from coding_agent.model import ModelTransportError
from coding_agent.protocol import Message, ModelTurn, RunStatus, ToolCall, ToolDefinition
from coding_agent.session import SessionError
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.screens import PluginManagementScreen, SkillManagementScreen
from coding_agent.tui.widgets import ActivityPane, Composer, ConversationPane
from tests.fakes import FakeModelClient


FIXTURE = Path(__file__).parent / "fixtures" / "tui_demo" / "buggy_project"
PLUGIN_EXAMPLE = Path("examples/plugins/git-readonly")


def _command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace.resolve(),
        "https://example.test/v1",
        "fake",
        "offline-secret",
        "FAKE_KEY",
        "disabled",
        frozenset({"FAKE_KEY"}),
        12,
        30_000,
        4,
        2_000,
        10,
    )


class StreamingParentClient:
    def __init__(self, script: Sequence[ModelTurn]) -> None:
        self.script = list(script)
        self.calls: list[tuple[tuple[Message, ...], tuple[ToolDefinition, ...]]] = []
        self.closed = False

    def complete_streaming(self, messages, tools, sink) -> ModelTurn:
        self.calls.append((tuple(messages), tuple(tools)))
        if not self.script:
            raise AssertionError("streaming script exhausted")
        turn = self.script.pop(0)
        if turn.final_text:
            midpoint = max(1, len(turn.final_text) // 2)
            sink(turn.final_text[:midpoint])
            sink(turn.final_text[midpoint:])
        return turn

    def complete(self, messages, tools) -> ModelTurn:
        self.calls.append((tuple(messages), tuple(tools)))
        return ModelTurn(
            json.dumps(
                {
                    "candidates": [
                        {
                            "key": "test.command",
                            "content": _command("-m", "pytest", "-q"),
                            "kind": "command",
                            "source": "TOOL_VERIFIED",
                            "evidence": {
                                "tool_name": "execute_command",
                                "command": _command("-m", "pytest", "-q"),
                                "success": True,
                            },
                        }
                    ]
                }
            )
        )

    def close(self) -> None:
        self.closed = True


class ClientFactory:
    def __init__(self, parent, child_scripts: Sequence[ModelTurn] = ()) -> None:
        self.parent = parent
        self.child_scripts = list(child_scripts)
        self._created = 0
        self._lock = threading.Lock()

    def __call__(self, *_args):
        with self._lock:
            self._created += 1
            if self._created == 1:
                return self.parent
            if not self.child_scripts:
                raise AssertionError("unexpected child model creation")
            return FakeModelClient([self.child_scripts.pop(0)])


async def _wait_until_idle(app: CodingAgentApp, pilot, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while app._running_task and time.monotonic() < deadline:
        await pilot.pause(0.05)
    assert not app._running_task, "TUI task did not finish before timeout"
    await pilot.pause()


async def _submit_ui_command(app: CodingAgentApp, pilot, text: str) -> None:
    composer = app.query_one("#composer", Composer)
    composer.text = text
    await pilot.press("ctrl+enter")
    await pilot.pause()


def _install_tui_resources(workspace: Path, home: Path) -> None:
    skill = workspace / ".coding-agent" / "skills" / "tdd" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: tdd\ndescription: Test changes before completion.\n---\n\n"
        "Run focused tests before reporting completion.\n",
        encoding="utf-8",
    )
    shutil.copytree(PLUGIN_EXAMPLE, home / "plugins" / "git-readonly")


def _session_option_index(app: CodingAgentApp, session_id: str) -> int:
    options = app.query_one("#session-list", OptionList)
    return next(
        index
        for index in range(options.option_count)
        if options.get_option_at_index(index).id == session_id
    )


async def _right_click_session(app: CodingAgentApp, pilot, session_id: str) -> None:
    index = _session_option_index(app, session_id)
    await pilot.click("#session-list", offset=(2, index * 2 + 1), button=3)
    await pilot.pause()


def test_real_product_repairs_with_tools_three_subagents_and_resumes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE, workspace)
    home = tmp_path / "home"
    _install_tui_resources(workspace, home)
    test_command = _command("-m", "pytest", "-q")
    parent = StreamingParentClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("before", "execute_command", json.dumps({"command": test_command})),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "delegate",
                        "delegate_tasks",
                        json.dumps(
                            {
                                "tasks": [
                                    {"task": "Inspect parser.py", "role": "explore"},
                                    {"task": "Inspect test_parser.py", "role": "analysis"},
                                    {"task": "Review the minimal fix", "role": "review"},
                                ]
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "replace_in_file",
                        json.dumps(
                            {
                                "path": "parser.py",
                                "old_text": '    normalized = text.encode("ascii").decode("ascii")',
                                "new_text": "    normalized = text",
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall("after", "execute_command", json.dumps({"command": test_command})),
                )
            ),
            ModelTurn(tool_calls=(ToolCall("git", "git_status", "{}"),)),
            ModelTurn("## Fixed\n\nUnicode parsing is fixed and the test passes."),
        ]
    )
    factory = ClientFactory(
        parent,
        (
            ModelTurn("The ASCII round-trip rejects Unicode."),
            ModelTurn("The test requires the original Unicode text."),
            ModelTurn("Removing only the round-trip is the minimal fix."),
        ),
    )
    service = CodingAgentService.create(_config(workspace), "custom", home, factory)
    original_session = service.snapshot().status.session_id

    async def first_launch() -> None:
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await _submit_ui_command(app, pilot, "/skills")
            assert isinstance(app.screen, SkillManagementScreen)
            await pilot.click("#resource-primary")
            await pilot.click("#resource-close")
            assert service.snapshot().status.active_skills == ("tdd",)

            await _submit_ui_command(app, pilot, "/plugins")
            assert isinstance(app.screen, PluginManagementScreen)
            await pilot.click("#resource-primary")
            await pilot.click("#resource-close")
            assert service.snapshot().status.enabled_plugins == ("git-readonly",)

            composer = app.query_one("#composer", Composer)
            composer.text = "Fix the Unicode parser failure using parallel investigation."
            await pilot.press("ctrl+enter")
            await _wait_until_idle(app, pilot)
            pane = app.query_one("#conversation", ConversationPane)
            activity = app.query_one("#activity", ActivityPane)
            assert "Unicode parsing is fixed" in pane.plain_text
            assert all(
                f"subagent-{index}" in activity.plain_text for index in (1, 2, 3)
            )
            assert "parser.py" in activity.plain_text
            assert "passed" in activity.plain_text
            assert "[command] execute_command" in activity.plain_text
            assert "[plugin:git-readonly] git_status" in activity.plain_text
            assert "subagent-1" not in pane.plain_text
            assert "git_status" not in pane.plain_text
            assert composer.display and pane.display
            assert service.snapshot().status.memory_count == 1
            assert "[memory] Added test.command" in activity.plain_text
            await _submit_ui_command(app, pilot, "/skills")
            await pilot.click("#resource-secondary")
            await pilot.click("#resource-close")
            assert service.snapshot().status.active_skills == ()
            app.exit()

    asyncio.run(first_launch())
    assert "encode" not in (workspace / "parser.py").read_text(encoding="utf-8")
    assert parent.closed

    resumed = CodingAgentService.create(
        _config(workspace),
        "custom",
        home,
        ClientFactory(FakeModelClient([])),
    )

    async def second_launch() -> None:
        app = CodingAgentApp(resumed)
        async with app.run_test(size=(120, 36)) as pilot:
            snapshot = resumed.snapshot()
            assert snapshot.status.session_id == original_session
            assert snapshot.status.memory_count == 1
            assert snapshot.status.enabled_plugins == ("git-readonly",)
            assert any("Unicode parsing is fixed" in item.content for item in snapshot.conversation)
            assert "[plugin:git-readonly] git_status" in app.query_one(
                "#activity", ActivityPane
            ).plain_text
            await pilot.press("ctrl+n")
            new_snapshot = resumed.snapshot()
            assert new_snapshot.status.session_id != original_session
            assert new_snapshot.conversation == ()
            assert new_snapshot.status.memory_count == 1
            app.handle_session_selected(original_session)
            assert any(
                "Unicode parsing is fixed" in item.content
                for item in resumed.snapshot().conversation
            )
            app.exit()

    asyncio.run(second_launch())


def test_real_session_context_menu_rename_delete_new_and_agent_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    client = FakeModelClient([ModelTurn("Session UX agent response.")])
    service = CodingAgentService.create(
        _config(workspace), "custom", home, ClientFactory(client)
    )
    session_a = service.snapshot().status.session_id
    service.rename_session("Session A")
    session_b = service.new_session().session_id
    service.rename_session("Session B")
    service.add_memory("build.system = cmake")

    async def scenario() -> None:
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            assert {item.session_id for item in service.list_sessions()} == {
                session_a,
                session_b,
            }

            await _right_click_session(app, pilot, session_b)
            menu = app.query_one("#session-context-menu", OptionList)
            menu.highlighted = 0
            await pilot.press("enter")
            rename_input = app.screen.query_one("#rename-session-value", Input)
            rename_input.value = "parser-fix"
            await pilot.press("enter")
            await pilot.pause()
            assert service.store.load_session(session_b, workspace).name == "parser-fix"
            assert "parser-fix" in "\n".join(
                str(app.query_one("#session-list", OptionList).get_option_at_index(i).prompt)
                for i in range(2)
            )

            session_a_index = _session_option_index(app, session_a)
            await pilot.click(
                "#session-list", offset=(2, session_a_index * 2 + 1)
            )
            await pilot.pause()
            assert service.snapshot().status.session_id == session_a

            await _right_click_session(app, pilot, session_b)
            menu.highlighted = 1
            await pilot.press("enter")
            await pilot.press("escape")
            assert service.store.load_session(session_b, workspace).name == "parser-fix"

            await _right_click_session(app, pilot, session_b)
            menu.highlighted = 1
            await pilot.press("enter")
            await pilot.click("#delete-session-confirm")
            await pilot.pause()
            with pytest.raises(SessionError) as deleted:
                service.store.load_session(session_b, workspace)
            assert deleted.value.error_code == "SESSION_NOT_FOUND"
            assert service.snapshot().status.session_id == session_a

            await pilot.press("ctrl+n")
            created = service.snapshot().status.session_id
            assert created not in {session_a, session_b}
            composer = app.query_one("#composer", Composer)
            assert app.focused is composer
            assert service.snapshot().conversation == ()
            assert service.snapshot().status.memory_count == 1

            composer.text = "Confirm the coding agent still responds."
            await pilot.press("ctrl+enter")
            await _wait_until_idle(app, pilot)
            assert "Session UX agent response" in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            assert service.snapshot().status.memory_count == 1
            app.exit()

    asyncio.run(scenario())


def test_real_product_recovers_after_provider_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = FakeModelClient(
        [
            ModelTransportError("provider unavailable"),
            ModelTurn("Retry completed."),
        ]
    )
    service = CodingAgentService.create(
        _config(workspace), "custom", tmp_path / "home", ClientFactory(client)
    )

    async def scenario() -> None:
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "first attempt"
            await pilot.press("ctrl+enter")
            await _wait_until_idle(app, pilot)
            assert composer.disabled is False
            failure_text = app.query_one("#activity", ActivityPane).plain_text
            assert "Provider Error" in failure_text
            assert "provider unavailable" in failure_text
            composer.text = "retry"
            await pilot.press("ctrl+enter")
            await _wait_until_idle(app, pilot)
            assert "Retry completed" in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            app.exit()

    asyncio.run(scenario())


def test_real_product_cancellation_does_not_commit_and_can_continue(tmp_path: Path) -> None:
    class BlockingClient(FakeModelClient):
        def __init__(self) -> None:
            super().__init__([ModelTurn("discarded"), ModelTurn("continued")])
            self.started = threading.Event()
            self.release = threading.Event()

        def complete(self, messages, tools) -> ModelTurn:
            if not self.calls:
                self.started.set()
                assert self.release.wait(timeout=5)
            return super().complete(messages, tools)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = BlockingClient()
    service = CodingAgentService.create(
        _config(workspace), "custom", tmp_path / "home", ClientFactory(client)
    )

    async def scenario() -> None:
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "cancel this"
            await pilot.press("ctrl+enter")
            assert await asyncio.to_thread(client.started.wait, 2)
            await pilot.press("ctrl+c")
            client.release.set()
            await _wait_until_idle(app, pilot)
            assert service.snapshot().conversation == ()
            composer.text = "continue now"
            await pilot.press("ctrl+enter")
            await _wait_until_idle(app, pilot)
            assert any(
                item.content == "continued"
                for item in service.snapshot().conversation
            )
            app.exit()

    asyncio.run(scenario())

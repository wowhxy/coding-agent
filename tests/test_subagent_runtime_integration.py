from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from coding_agent.cli import main
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.protocol import ModelTurn, Role, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import SubagentEvent, SubagentRequest
from fakes import FakeModelClient
from test_subagent_cli import (
    API_KEY,
    ClosableFakeModelClient,
    QueueClientFactory,
    _arguments,
    _enable_example_plugin,
    _environment,
    _message_text,
    _write_skill,
)


def test_subagent_events_are_aggregated_on_the_delegating_thread(tmp_path) -> None:
    main_thread = threading.get_ident()
    event_threads: list[int] = []
    events: list[SubagentEvent] = []

    def sink(event: SubagentEvent) -> None:
        event_threads.append(threading.get_ident())
        events.append(event)

    manager = SubagentManager(
        tmp_path,
        lambda: FakeModelClient([ModelTurn("done")]),
        event_sink=sink,
    )

    manager.delegate((SubagentRequest("one"), SubagentRequest("two")))

    assert event_threads and set(event_threads) == {main_thread}
    assert tuple(event.kind for event in events) == (
        "batch_started",
        "task_started",
        "task_started",
        "task_completed",
        "task_completed",
        "batch_collected",
    )
    assert all("done" not in event.message for event in events)


class WaitingInput:
    def __init__(self, store: JsonSessionStore, workspace: Path) -> None:
        self._commands = iter(("/skill use inspect-project", "/background inspect parser"))
        self.store = store
        self.workspace = workspace
        self.waited = False

    def __call__(self, _prompt: str) -> str:
        try:
            return next(self._commands)
        except StopIteration:
            if self.waited:
                return "/exit"
            self.waited = True
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                latest = self.store.load_latest(self.workspace)
                if latest is not None and any(
                    message.role is Role.ASSISTANT
                    and message.content == "background done"
                    for message in latest.messages
                ):
                    return "/jobs"
                time.sleep(0.01)
            raise AssertionError("background delegation did not persist")


def test_background_runtime_has_isolated_manager_and_submit_time_parent_plugins(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home)
    WorkspaceMemoryStore(home).add(
        workspace,
        "python -m pytest -q",
        (),
        kind="command",
        key="test.command",
    )
    _enable_example_plugin(home, workspace)
    foreground = ClosableFakeModelClient([])
    background_parent = ClosableFakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "background-delegate",
                        "delegate_tasks",
                        json.dumps(
                            {"tasks": [{"task": "inspect background context"}]}
                        ),
                    ),
                )
            ),
            ModelTurn("background done"),
        ]
    )
    child = ClosableFakeModelClient([ModelTurn("background child finding")])
    factory = QueueClientFactory(foreground, background_parent, child)
    store = JsonSessionStore(home)

    exit_code = main(
        _arguments(workspace),
        environ=_environment(home),
        client_factory=factory,
        input_reader=WaitingInput(store, workspace),
    )

    assert exit_code == 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (
        background_parent.closed and child.closed
    ):
        time.sleep(0.01)
    assert foreground.closed and background_parent.closed and child.closed
    parent_definitions = tuple(item.name for item in background_parent.calls[0][1])
    assert "delegate_tasks" in parent_definitions
    assert "git_status" in parent_definitions
    assert tuple(item.name for item in child.calls[0][1]) == (
        "list_files",
        "search_text",
        "read_file",
    )
    child_text = _message_text(child)
    assert "python -m pytest -q" in child_text
    assert "SKILL_PRIVATE_GUIDANCE" in child_text
    summaries = store.list_sessions(workspace)
    assert len(summaries) == 1
    saved = store.load_session(summaries[0].session_id, workspace)
    assert any(
        message.role is Role.ASSISTANT and message.content == "background done"
        for message in saved.messages
    )

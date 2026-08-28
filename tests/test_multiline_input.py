from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, Role, RunResult, RunStatus
from coding_agent.session_store import JsonSessionStore


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


class _Runner:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
        self.tasks.append(user_message)
        history.append(Message(Role.USER, user_message))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)


def _shell(tmp_path: Path, inputs: tuple[object, ...]):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "111111111111"
    )
    runner = _Runner()
    session = InteractiveSession(
        runner,  # type: ignore[arg-type]
        ConversationHistory("system"),
        store.create_session(workspace, "p", "m"),
        store,
        "p",
        "m",
        (),
    )
    iterator = iter(inputs)

    def read(_prompt: str) -> str:
        item = next(iterator)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item

    output: list[str] = []
    return InteractiveShell(session, store, read, output.append), runner, output


def test_multiline_preserves_lines_and_send_submits_once(tmp_path: Path) -> None:
    shell, runner, _ = _shell(
        tmp_path, ("/multiline", "first line", "  indented line  ", "/send", "/exit")
    )

    assert shell.run() == 0
    assert runner.tasks == ["first line\n  indented line  "]


@pytest.mark.parametrize(
    "ending",
    ("/cancel", KeyboardInterrupt(), EOFError()),
)
def test_multiline_cancel_interrupt_and_eof_discard_buffer(
    tmp_path: Path, ending: object
) -> None:
    shell, runner, output = _shell(
        tmp_path, ("/multiline", "discard this", ending, "single task", "/exit")
    )

    assert shell.run() == 0
    assert runner.tasks == ["single task"]
    assert any("cancelled" in line for line in output)


def test_empty_multiline_does_not_call_model_and_next_single_line_works(tmp_path: Path) -> None:
    shell, runner, _ = _shell(
        tmp_path, ("/multiline", "", "   ", "/send", "single", "/exit")
    )

    assert shell.run() == 0
    assert runner.tasks == ["single"]

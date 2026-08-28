from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, Role, RunResult, RunStatus, ToolCall
from coding_agent.session import SessionError
from coding_agent.session_store import JsonSessionStore


BASE = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> tuple[JsonSessionStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    times = iter(BASE + timedelta(minutes=i) for i in range(20))
    return (
        JsonSessionStore(
            tmp_path / "session-home",
            clock=lambda: next(times),
            id_generator=lambda: next(ids),
        ),
        workspace,
    )


def test_list_sessions_is_newest_first_and_marks_latest(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    first = store.save(
        replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "first"),))
    )
    second = store.rename_session(
        replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "second"),)),
        "Second task",
    )

    summaries = store.list_sessions(workspace)

    assert [item.session_id for item in summaries] == [second.session_id, first.session_id]
    assert summaries[0].name == "Second task"
    assert [item.is_latest for item in summaries] == [True, False]


def test_search_sessions_casefolds_unicode_name_and_complete_protocol_text(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    call = ToolCall("call-1", "read_file", '{"path":"STRASSE.py"}')
    record = replace(
        store.create_session(workspace, "p", "m"),
        messages=(
            Message(Role.USER, "检查文件"),
            Message(Role.ASSISTANT, tool_calls=(call,)),
            Message(Role.TOOL, "修复完成", tool_call_id="call-1"),
        ),
    )
    saved = store.rename_session(record, "Straße 修复")

    assert [item.session_id for item in store.search_sessions(workspace, "STRASSE")] == [saved.session_id]
    assert [item.session_id for item in store.search_sessions(workspace, "修复完成")] == [saved.session_id]
    assert [item.session_id for item in store.search_sessions(workspace, "strasse.py")] == [saved.session_id]
    assert store.search_sessions(workspace, "absent") == ()


def test_list_does_not_hide_corrupt_indexed_session(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    saved = store.save(
        replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "task"),))
    )
    (store.root / "sessions" / f"{saved.session_id}.json").write_text("{", encoding="utf-8")

    with pytest.raises(SessionError) as raised:
        store.list_sessions(workspace)

    assert raised.value.error_code == "SESSION_CORRUPT"


class _Runner:
    def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
        history.append(Message(Role.USER, user_message))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)


def test_shell_lists_searches_and_switches_only_current_workspace(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    first = store.rename_session(store.create_session(workspace, "p", "m"), "Alpha")
    second = store.rename_session(store.create_session(workspace, "p", "m"), "Beta 中文")
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other = store.rename_session(
        store.create_session(other_workspace, "p", "m"), "Beta outside"
    )
    session = InteractiveSession(
        _Runner(),  # type: ignore[arg-type]
        ConversationHistory("system"),
        second,
        store,
        "p",
        "m",
        (),
    )
    commands = iter(("/sessions", "/search beta", f"/use {first.session_id}", f"/use {other.session_id}", "/exit"))
    output: list[str] = []

    assert InteractiveShell(session, store, lambda _prompt: next(commands), output.append).run() == 0
    assert session.record.session_id == first.session_id
    assert any("* " + second.session_id in line for line in output)
    assert any("Beta 中文" in line for line in output)
    assert any("SESSION_WORKSPACE_MISMATCH" in line for line in output)

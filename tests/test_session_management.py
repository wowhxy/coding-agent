from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import coding_agent.session_store as session_store
from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, Role, RunResult, RunStatus
from coding_agent.session import SessionError, deserialize_session, serialize_session
from coding_agent.session_store import JsonSessionStore


CREATED = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 8, 28, 8, 1, tzinfo=timezone.utc)


def _v1_document(workspace: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_id": "111111111111",
        "workspace": str(workspace.resolve()),
        "provider": "provider",
        "model": "model",
        "created_at": "2026-08-28T08:00:00Z",
        "updated_at": "2026-08-28T08:01:00Z",
        "messages": [{"role": "user", "content": "old task"}],
    }


def test_v1_session_migrates_to_optional_fields_and_serializes_as_v5(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    record = deserialize_session(json.dumps(_v1_document(workspace)))
    encoded = json.loads(serialize_session(record))

    assert record.name is None
    assert encoded["schema_version"] == 5
    assert encoded["name"] is None
    assert encoded["name_source"] is None
    assert encoded["summary"] is None


def test_v2_allows_empty_history_for_named_new_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        tmp_path / "session-home",
        clock=lambda: CREATED,
        id_generator=lambda: "111111111111",
    )

    renamed = store.rename_session(
        store.create_session(workspace, "provider", "model"), "  调试任务  "
    )

    assert renamed.name == "调试任务"
    assert renamed.messages == ()
    assert store.load_latest(workspace) == renamed


def test_delete_latest_selects_previous_and_delete_last_leaves_empty_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222"))
    times = iter((CREATED, CREATED, UPDATED, UPDATED))
    store = JsonSessionStore(
        tmp_path / "session-home",
        clock=lambda: next(times),
        id_generator=lambda: next(ids),
    )
    first = store.save(replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "one"),)))
    second = store.save(replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "two"),)))

    assert store.delete_session(second.session_id, workspace) == first
    assert store.load_latest(workspace) == first
    assert store.delete_session(first.session_id, workspace) is None
    assert store.load_latest(workspace) is None


def test_delete_restores_session_when_index_update_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "session-home"
    store = JsonSessionStore(root, clock=lambda: CREATED, id_generator=lambda: "111111111111")
    saved = store.save(
        replace(store.create_session(workspace, "p", "m"), messages=(Message(Role.USER, "task"),))
    )
    real_write = session_store._atomic_write_text

    def fail_index(path: Path, text: str) -> None:
        if path.parent.name == "workspaces":
            raise PermissionError("private host detail")
        real_write(path, text)

    monkeypatch.setattr(session_store, "_atomic_write_text", fail_index)

    with pytest.raises(SessionError) as raised:
        store.delete_session(saved.session_id, workspace)

    assert raised.value.error_code == "SESSION_SAVE_FAILED"
    assert store.load_session(saved.session_id, workspace) == saved


class _Runner:
    def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
        history.append(Message(Role.USER, user_message))
        history.append(Message(Role.ASSISTANT, f"done: {user_message}"))
        return RunResult(RunStatus.FINAL_RESPONSE, f"done: {user_message}", 1, None)


def test_shell_new_rename_delete_and_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    store = JsonSessionStore(
        tmp_path / "session-home",
        clock=lambda: CREATED,
        id_generator=lambda: next(ids),
    )
    initial = store.create_session(workspace, "provider", "model")
    interactive = InteractiveSession(
        runner=_Runner(),  # type: ignore[arg-type]
        history=ConversationHistory("system"),
        record=initial,
        store=store,
        provider="provider",
        model="model",
        sensitive_values=(),
    )
    commands = iter(("first", "/new", "/rename second task", "second", "/delete", "/exit"))
    output: list[str] = []
    shell = InteractiveShell(interactive, store, lambda _prompt: next(commands), output.append)

    assert shell.run() == 0
    assert interactive.record.session_id == "111111111111"
    assert interactive.history.persisted_messages[-1] == Message(Role.ASSISTANT, "done: first")
    assert store.load_latest(workspace).session_id == "111111111111"
    assert any("222222222222" in line for line in output)
    assert any("second task" in line for line in output)

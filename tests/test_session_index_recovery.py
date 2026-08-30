from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import coding_agent.session_index as session_index
import coding_agent.session_store as session_store
from coding_agent.protocol import Message, Role, ToolCall, ToolResult
from coding_agent.session import serialize_session
from coding_agent.session_store import JsonSessionStore


BASE = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> tuple[JsonSessionStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    times = iter(BASE + timedelta(seconds=index) for index in range(20))
    return (
        JsonSessionStore(
            tmp_path / "home",
            clock=lambda: next(times),
            id_generator=lambda: next(ids),
        ),
        workspace,
    )


def _saved(store: JsonSessionStore, workspace: Path, text: str):
    return store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, text), Message(Role.ASSISTANT, "done")),
        )
    )


def test_deleting_derived_db_keeps_exact_history_and_rebuilds_search(
    tmp_path: Path,
) -> None:
    store, workspace = _store(tmp_path)
    saved = _saved(store, workspace, "unicode parser marker")
    canonical_path = store.root / "sessions" / f"{saved.session_id}.json"
    before = canonical_path.read_bytes()
    index_path = session_index.SessionIndex(store.root, workspace.resolve()).database_path
    index_path.unlink()

    restarted = JsonSessionStore(store.root)
    resumed = restarted.load_latest(workspace)

    assert resumed is not None
    assert resumed.messages == saved.messages
    assert restarted.last_report.latest_fast_path_used is True
    assert not index_path.exists()

    assert restarted.search_session_results(workspace, "unicode parser marker")
    assert restarted.last_report.index_rebuilt is True
    assert canonical_path.read_bytes() == before


def test_corrupt_and_schema_incompatible_indexes_rebuild_from_canonical(
    tmp_path: Path,
) -> None:
    store, workspace = _store(tmp_path)
    saved = _saved(store, workspace, "rebuild marker")
    path = session_index.SessionIndex(store.root, workspace.resolve()).database_path

    path.write_bytes(b"not sqlite")
    assert JsonSessionStore(store.root).search_session_results(workspace, "rebuild marker")

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE metadata SET value = '999' WHERE key = 'schema_version'"
        )
    restarted = JsonSessionStore(store.root)
    assert restarted.list_sessions(workspace)[0].session_id == saved.session_id
    assert restarted.last_report.index_rebuilt is True


def test_derived_update_failure_does_not_undo_canonical_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, workspace = _store(tmp_path)
    first = _saved(store, workspace, "first")

    def fail_upsert(self, record):
        raise sqlite3.OperationalError("derived failure")

    monkeypatch.setattr(session_index.SessionIndex, "upsert", fail_upsert)
    second = _saved(store, workspace, "second")

    assert store.load_session(second.session_id, workspace).messages[-2].content == "second"
    assert store.load_latest(workspace).session_id == second.session_id
    assert (
        session_index.SessionIndex(store.root, workspace.resolve()).stale_path.exists()
    )
    assert store.load_session(first.session_id, workspace).messages[-2].content == "first"


def test_latest_pointer_write_failure_uses_catalog_on_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, workspace = _store(tmp_path)
    _saved(store, workspace, "first")
    real_write = session_store._atomic_write_text

    def fail_latest(path: Path, text: str) -> None:
        if path.name == "latest":
            raise PermissionError("pointer unavailable")
        real_write(path, text)

    monkeypatch.setattr(session_store, "_atomic_write_text", fail_latest)
    latest = _saved(store, workspace, "second")
    store.latest_pointer_path(workspace).unlink(missing_ok=True)
    monkeypatch.setattr(session_store, "_atomic_write_text", real_write)

    restarted = JsonSessionStore(store.root)
    assert restarted.load_latest(workspace).session_id == latest.session_id
    assert restarted.last_report.latest_fast_path_used is False


def test_contentless_fts_stores_locators_not_second_message_copy(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    call = ToolCall("tool-1", "execute_command", '{"command":"pytest -q"}')
    marker = "UNIQUE_CANONICAL_MESSAGE_MARKER"
    store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(
                Message(Role.USER, marker),
                Message(Role.ASSISTANT, tool_calls=(call,)),
                Message(
                    Role.TOOL,
                    ToolResult("tool-1", "execute_command", True, "passed").as_message_content(),
                    tool_call_id="tool-1",
                ),
            ),
        )
    )
    path = session_index.SessionIndex(store.root, workspace.resolve()).database_path

    with closing(sqlite3.connect(path)) as connection, connection:
        stored_content = connection.execute("SELECT search_text FROM search_fts").fetchall()
        locator_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(search_locators)")
        }

    assert stored_content and all(row[0] is None for row in stored_content)
    assert "projection" not in locator_columns
    assert "content" not in locator_columns


def test_legacy_workspace_index_migrates_without_rewriting_session(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    record = replace(
        store.create_session(workspace, "fake", "model"),
        messages=(Message(Role.USER, "legacy marker"),),
    )
    session_path = store.root / "sessions" / f"{record.session_id}.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(serialize_session(record), encoding="utf-8")
    before = session_path.read_bytes()
    legacy_path = store._index_path(workspace.resolve())
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace": str(workspace.resolve()),
                "latest_session_id": record.session_id,
                "session_ids": [record.session_id],
            }
        ),
        encoding="utf-8",
    )

    restarted = JsonSessionStore(store.root)
    loaded = restarted.load_latest(workspace)

    assert loaded is not None and loaded.session_id == record.session_id
    assert session_path.read_bytes() == before
    assert restarted.latest_pointer_path(workspace).exists()

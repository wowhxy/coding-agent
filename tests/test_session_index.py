from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding_agent.protocol import Message, Role
from coding_agent.session import SessionNameSource
from coding_agent.session_store import JsonSessionStore


BASE = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path, *, count: int = 12) -> tuple[JsonSessionStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    ids = iter(f"{index:012x}" for index in range(1, count + 1))
    times = iter(BASE + timedelta(minutes=index) for index in range(count * 3))
    return (
        JsonSessionStore(
            tmp_path / "home",
            clock=lambda: next(times),
            id_generator=lambda: next(ids),
        ),
        workspace,
    )


def _persist(store: JsonSessionStore, workspace: Path, label: str):
    return store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, label),),
            name=label,
            name_source=SessionNameSource.MANUAL,
        )
    )


def test_latest_pointer_restart_loads_only_target_session(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    _persist(store, workspace, "first")
    latest = _persist(store, workspace, "latest")

    restarted = JsonSessionStore(store.root)
    loaded = restarted.load_latest(workspace)

    assert loaded is not None
    assert loaded.session_id == latest.session_id
    assert loaded.messages == latest.messages
    assert restarted.last_report.latest_fast_path_used is True
    assert restarted.last_report.session_files_loaded == 1
    assert restarted.latest_pointer_path(workspace).read_text(encoding="utf-8") == (
        latest.session_id + "\n"
    )


@pytest.mark.parametrize("pointer", (None, "broken\n", "ffffffffffff\n"))
def test_invalid_latest_pointer_falls_back_to_catalog_and_repairs(
    tmp_path: Path, pointer: str | None
) -> None:
    store, workspace = _store(tmp_path)
    _persist(store, workspace, "first")
    latest = _persist(store, workspace, "latest")
    path = store.latest_pointer_path(workspace)
    if pointer is None:
        path.unlink()
    else:
        path.write_text(pointer, encoding="utf-8")

    restarted = JsonSessionStore(store.root)
    loaded = restarted.load_latest(workspace)

    assert loaded is not None
    assert loaded.session_id == latest.session_id
    assert restarted.last_report.latest_fast_path_used is False
    assert path.read_text(encoding="utf-8") == latest.session_id + "\n"


def test_catalog_list_is_paged_without_loading_canonical_histories(
    tmp_path: Path,
) -> None:
    store, workspace = _store(tmp_path)
    records = [_persist(store, workspace, f"session-{index}") for index in range(5)]

    restarted = JsonSessionStore(store.root)
    page = restarted.list_sessions(workspace, limit=2, offset=1)

    assert [item.session_id for item in page] == [
        records[3].session_id,
        records[2].session_id,
    ]
    assert restarted.last_report.full_history_files_loaded == 0
    assert restarted.last_report.catalog_entries_loaded == 2


def test_catalog_rename_and_delete_do_not_duplicate_ids(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    first = _persist(store, workspace, "first")
    second = _persist(store, workspace, "second")
    renamed = store.rename_session(first, "renamed", make_latest=False)

    items = store.list_sessions(workspace)

    assert [item.session_id for item in items] == [renamed.session_id, second.session_id]
    assert next(item for item in items if item.session_id == first.session_id).name == "renamed"

    selected = store.delete_session(second.session_id, workspace)

    assert selected is not None
    assert selected.session_id == first.session_id
    assert [item.session_id for item in store.list_sessions(workspace)] == [
        first.session_id
    ]
    assert store.latest_pointer_path(workspace).read_text(encoding="utf-8") == (
        first.session_id + "\n"
    )


def test_catalog_list_repairs_valid_but_dangling_pointer(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    latest = _persist(store, workspace, "latest")
    store.latest_pointer_path(workspace).write_text("ffffffffffff\n", encoding="utf-8")

    items = JsonSessionStore(store.root).list_sessions(workspace)

    assert [item.session_id for item in items if item.is_latest] == [latest.session_id]
    assert store.latest_pointer_path(workspace).read_text(encoding="utf-8") == (
        latest.session_id + "\n"
    )


def test_corrupt_latest_canonical_falls_back_to_next_valid_session(
    tmp_path: Path,
) -> None:
    store, workspace = _store(tmp_path)
    first = _persist(store, workspace, "first")
    latest = _persist(store, workspace, "latest")
    (store.root / "sessions" / f"{latest.session_id}.json").write_text(
        "{", encoding="utf-8"
    )

    loaded = JsonSessionStore(store.root).load_latest(workspace)

    assert loaded is not None
    assert loaded.session_id == first.session_id
    assert store.latest_pointer_path(workspace).read_text(encoding="utf-8") == (
        first.session_id + "\n"
    )

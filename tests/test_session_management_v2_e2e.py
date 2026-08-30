from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coding_agent.protocol import Message, Role, ToolCall, ToolResult
from coding_agent.recall import RecallService
from coding_agent.session import SessionNameSource
from coding_agent.session_index import SessionIndex
from coding_agent.session_store import JsonSessionStore


def test_session_management_v2_locator_first_end_to_end(tmp_path: Path) -> None:
    workspace = tmp_path / "cpp"
    other = tmp_path / "python"
    workspace.mkdir()
    other.mkdir()
    identifiers = iter(f"{index:012x}" for index in range(1, 80))
    moments = iter(
        datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        + timedelta(seconds=index)
        for index in range(200)
    )
    store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: next(moments),
        id_generator=lambda: next(identifiers),
    )
    records = []
    recall_target = None
    search_target = None
    for index in range(30):
        messages = (Message(Role.USER, f"ordinary task {index}"), Message(Role.ASSISTANT, "done"))
        name = f"Task {index}"
        if index == 7:
            call = ToolCall("unicode-tool", "execute_command", '{"command":"pytest -q"}')
            messages = (
                Message(Role.USER, "investigate unicode parser"),
                Message(Role.ASSISTANT, tool_calls=(call,)),
                Message(
                    Role.TOOL,
                    ToolResult(
                        "unicode-tool",
                        "execute_command",
                        False,
                        "test_parser_unicode failed",
                        "COMMAND_FAILED",
                        "exit 1",
                    ).as_message_content(),
                    tool_call_id="unicode-tool",
                ),
            )
            name = "legacyuniquetoken"
        record = store.save(
            replace(
                store.create_session(workspace, "fake", "model"),
                name=name,
                name_source=SessionNameSource.MANUAL,
                messages=messages,
            )
        )
        records.append(record)
        if index == 7:
            search_target = record
            recall_target = record

    outside = store.save(
        replace(
            store.create_session(other, "fake", "model"),
            messages=(Message(Role.USER, "unicode parser outside workspace"),),
        )
    )
    assert search_target is not None and recall_target is not None
    latest = records[-1]
    latest_before = (store.root / "sessions" / f"{latest.session_id}.json").read_bytes()

    restarted = JsonSessionStore(store.root)
    assert restarted.load_latest(workspace).session_id == latest.session_id
    assert restarted.last_report.latest_fast_path_used is True
    assert restarted.last_report.session_files_loaded == 1

    page = restarted.list_sessions(workspace, limit=10, offset=10)
    assert len(page) == 10
    assert restarted.last_report.full_history_files_loaded == 0
    assert restarted.last_report.catalog_entries_loaded == 10

    hits = restarted.search_session_results(workspace, "unicode parser", limit=10)
    assert hits
    assert {item.session_id for item in hits} == {search_target.session_id}
    assert all(item.session_id != outside.session_id for item in hits)
    assert restarted.last_report.search_backend == "fts5"
    assert restarted.last_report.session_files_loaded == 1

    opened = restarted.load_session(search_target.session_id, workspace)
    assert opened.messages == search_target.messages
    assert restarted.last_report.session_files_loaded == 1

    renamed = restarted.rename_session(opened, "currentuniquetoken", make_latest=False)
    assert restarted.search_sessions(workspace, "legacyuniquetoken") == ()
    assert [item.session_id for item in restarted.search_sessions(
        workspace, "currentuniquetoken"
    )] == [renamed.session_id]

    recalled = RecallService(restarted).search(workspace, "last unicode parser failed")
    assert any(item.session_id == recall_target.session_id for item in recalled)
    assert any("test_parser_unicode failed" in item.excerpt for item in recalled)

    restarted.delete_session(renamed.session_id, workspace)
    assert restarted.search_sessions(workspace, "currentuniquetoken") == ()

    index = SessionIndex(store.root, workspace.resolve())
    index.database_path.unlink()
    resumed = JsonSessionStore(store.root)
    assert resumed.load_latest(workspace).messages == latest.messages
    assert resumed.last_report.latest_fast_path_used is True
    assert not index.database_path.exists()
    assert resumed.search_session_results(workspace, "ordinary task 20")
    assert resumed.last_report.index_rebuilt is True
    assert (store.root / "sessions" / f"{latest.session_id}.json").read_bytes() == latest_before

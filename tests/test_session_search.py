from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coding_agent.protocol import Message, Role, ToolCall, ToolResult
from coding_agent.session import SessionNameSource
from coding_agent.session_store import JsonSessionStore


BASE = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> tuple[JsonSessionStore, Path, Path]:
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    ids = iter(f"{index:012x}" for index in range(1, 30))
    times = iter(BASE + timedelta(seconds=index) for index in range(100))
    return (
        JsonSessionStore(
            tmp_path / "home",
            clock=lambda: next(times),
            id_generator=lambda: next(ids),
        ),
        workspace,
        other,
    )


def _save(
    store: JsonSessionStore,
    workspace: Path,
    name: str,
    messages: tuple[Message, ...],
):
    return store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            name=name,
            name_source=SessionNameSource.MANUAL,
            messages=messages,
        )
    )


def test_fts_search_handles_unicode_chinese_paths_and_workspace_isolation(
    tmp_path: Path,
) -> None:
    store, workspace, other = _store(tmp_path)
    call = ToolCall("call-1", "read_file", '{"path":"src/STRASSE.py"}')
    target = _save(
        store,
        workspace,
        "Unicode 修复",
        (
            Message(Role.USER, "检查 Unicode parser 中文路径"),
            Message(Role.ASSISTANT, tool_calls=(call,)),
            Message(
                Role.TOOL,
                ToolResult("call-1", "read_file", True, "source body").as_message_content(),
                tool_call_id="call-1",
            ),
            Message(Role.ASSISTANT, "修复完成：Unicode parser now passes"),
        ),
    )
    _save(store, other, "outside", (Message(Role.USER, "Unicode parser 中文路径"),))

    for query in ("STRASSE.py", "修复完成", "Unicode parser", "src/strasse.py"):
        results = store.search_session_results(workspace, query)
        assert results
        assert {item.session_id for item in results} == {target.session_id}
        assert all(len(item.snippet) <= 500 for item in results)
        assert store.last_report.search_backend == "fts5"
        assert store.last_report.session_files_loaded == 1


def test_multiple_hits_in_one_session_load_canonical_file_once(tmp_path: Path) -> None:
    store, workspace, _other = _store(tmp_path)
    target = _save(
        store,
        workspace,
        "parser work",
        (
            Message(Role.USER, "unicode parser failure"),
            Message(Role.ASSISTANT, "unicode parser diagnosis"),
            Message(Role.USER, "verify unicode parser"),
        ),
    )
    for index in range(5):
        _save(store, workspace, f"noise-{index}", (Message(Role.USER, "unrelated"),))

    results = store.search_session_results(workspace, "unicode parser", limit=10)

    assert results
    assert {item.session_id for item in results} == {target.session_id}
    assert store.last_report.session_files_loaded == 1
    assert store.last_report.search_hits == len(results)


def test_tool_projection_is_bounded_useful_and_secret_filtered(tmp_path: Path) -> None:
    store, workspace, _other = _store(tmp_path)
    read_call = ToolCall("read-1", "read_file", '{"path":"src/large.py"}')
    command_call = ToolCall(
        "cmd-1", "execute_command", '{"command":"python -m pytest tests/test_parser.py"}'
    )
    secret = "sk-" + "1234567890abcdefghijkl"
    _save(
        store,
        workspace,
        "verification",
        (
            Message(Role.USER, f"api_key={secret}"),
            Message(Role.ASSISTANT, tool_calls=(read_call, command_call)),
            Message(
                Role.TOOL,
                ToolResult(
                    "read-1", "read_file", True, "SOURCE_ONLY_NEEDLE" + "x" * 10_000
                ).as_message_content(),
                tool_call_id="read-1",
            ),
            Message(
                Role.TOOL,
                ToolResult(
                    "cmd-1",
                    "execute_command",
                    False,
                    "test_parser_unicode failed\n" + "x" * 10_000,
                    "COMMAND_FAILED",
                    "exit 1",
                ).as_message_content(),
                tool_call_id="cmd-1",
            ),
        ),
    )

    assert store.search_session_results(workspace, "test_parser_unicode failed")
    assert store.search_session_results(workspace, "tests/test_parser.py")
    assert store.search_session_results(workspace, "SOURCE_ONLY_NEEDLE") == ()
    assert store.search_session_results(workspace, secret) == ()


def test_fts_disabled_fallback_scans_each_session_at_most_once(tmp_path: Path) -> None:
    store, workspace, _other = _store(tmp_path)
    target = _save(
        store,
        workspace,
        "target",
        (
            Message(Role.USER, "fallback marker"),
            Message(Role.ASSISTANT, "fallback marker repeated"),
        ),
    )
    _save(store, workspace, "noise", (Message(Role.USER, "nothing"),))

    results = store.search_session_results(
        workspace, "fallback marker", limit=5, fts_enabled=False
    )

    assert results
    assert {item.session_id for item in results} == {target.session_id}
    assert store.last_report.search_backend == "scan"
    assert store.last_report.session_files_loaded == 2


def test_equal_bm25_hits_use_recency_then_id_as_deterministic_tie_break(
    tmp_path: Path,
) -> None:
    store, workspace, _other = _store(tmp_path)
    older = _save(store, workspace, "older", (Message(Role.USER, "ranking marker"),))
    newer = _save(store, workspace, "newer", (Message(Role.USER, "ranking marker"),))

    first = store.search_session_results(workspace, "ranking marker", limit=2)
    second = store.search_session_results(workspace, "ranking marker", limit=2)

    assert [item.session_id for item in first] == [newer.session_id, older.session_id]
    assert first == second

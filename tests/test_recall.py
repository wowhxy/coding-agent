from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import (
    Message,
    Role,
    RunResult,
    RunStatus,
    ToolCall,
    ToolResult,
)
from coding_agent.recall import RecallEntry, RecallService, should_automatic_recall
from coding_agent.session_store import JsonSessionStore


BASE = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> tuple[JsonSessionStore, Path, Path]:
    first = tmp_path / "cpp"
    second = tmp_path / "python"
    first.mkdir()
    second.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    times = iter(BASE + timedelta(minutes=index) for index in range(20))
    return (
        JsonSessionStore(
            tmp_path / "home",
            clock=lambda: next(times),
            id_generator=ids.__next__,
        ),
        first,
        second,
    )


def test_recall_searches_protocol_and_metadata_with_workspace_isolation(
    tmp_path: Path,
) -> None:
    store, workspace, other = _store(tmp_path)
    old = store.create_session(workspace, "fake", "model")
    old = store.rename_session(
        replace(
            old,
            messages=(
                Message(Role.USER, "debug the Unicode parser"),
                Message(
                    Role.ASSISTANT,
                    "I ran the focused test",
                    (ToolCall("tool-1", "execute_command", '{"command":"pytest"}'),),
                ),
                Message(
                    Role.TOOL,
                    ToolResult(
                        "tool-1",
                        "execute_command",
                        False,
                        "test_parser_unicode failed",
                        "COMMAND_FAILED",
                        "exit 1",
                    ).as_message_content(),
                    tool_call_id="tool-1",
                ),
            ),
        ),
        "Parser investigation",
    )
    outside = store.save(
        replace(
            store.create_session(other, "fake", "model"),
            messages=(Message(Role.USER, "Unicode parser outside"),),
        )
    )
    service = RecallService(store)

    results = service.search(workspace, "Unicode parser failed")

    assert results
    assert all(item.session_id == old.session_id for item in results)
    assert all(item.session_id != outside.session_id for item in results)
    assert any("test_parser_unicode failed" in item.excerpt for item in results)
    assert all(len(item.excerpt) <= 500 for item in results)
    assert all(item.timestamp.tzinfo is not None for item in results)


def test_missing_and_corrupt_fts_index_rebuilds_from_canonical_sessions(
    tmp_path: Path,
) -> None:
    store, workspace, _other = _store(tmp_path)
    saved = store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, "remember parser regression"),),
        )
    )
    service = RecallService(store)

    first = service.search(workspace, "parser regression")
    index_path = service.index_path(workspace)
    if service.backend == "fts5":
        assert index_path.exists()
        index_path.write_bytes(b"not a sqlite database")
    second = service.search(workspace, "parser regression")

    assert [item.session_id for item in first] == [saved.session_id]
    assert [item.session_id for item in second] == [saved.session_id]


def test_fts_fingerprint_detects_history_change_at_same_timestamp(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: BASE,
        id_generator=lambda: "111111111111",
    )
    record = store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, "first marker"),),
        )
    )
    service = RecallService(store)
    assert service.search(workspace, "first marker")
    if service.backend != "fts5":
        pytest.skip("SQLite FTS5 is unavailable")

    store.save(
        replace(record, messages=(Message(Role.USER, "second marker"),))
    )

    assert any(
        "second marker" in item.excerpt
        for item in service.search(workspace, "second marker")
    )


def test_fts_unavailable_uses_deterministic_scan_and_skips_malformed_session(
    tmp_path: Path,
) -> None:
    store, workspace, _other = _store(tmp_path)
    good = store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(
                Message(Role.USER, "old investigation"),
                Message(Role.ASSISTANT, "previous tokenizer failure"),
            ),
        )
    )
    bad = store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, "malformed target"),),
        )
    )
    (store.root / "sessions" / f"{bad.session_id}.json").write_text(
        "{", encoding="utf-8"
    )
    service = RecallService(store, fts_enabled=False)

    first = service.search(workspace, "tokenizer failure")
    second = service.search(workspace, "tokenizer failure")

    assert service.backend == "scan"
    assert first == second
    assert [item.session_id for item in first] == [good.session_id]
    assert service.search(workspace, "no such detail") == ()


def test_automatic_recall_is_conservative() -> None:
    assert should_automatic_recall("上次 Unicode parser 怎么修的？") is True
    assert should_automatic_recall("Use the previous test result") is True
    assert should_automatic_recall("fix the Unicode parser") is False


class _RecallRunner:
    def __init__(self) -> None:
        self.recall_sets: list[tuple[RecallEntry, ...]] = []

    def set_recalled_history(self, entries: tuple[RecallEntry, ...]) -> None:
        self.recall_sets.append(entries)

    def run_turn(self, history: ConversationHistory, text: str) -> RunResult:
        history.append(Message(Role.USER, text))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)


class _RecallService:
    def __init__(self, entry: RecallEntry) -> None:
        self.entry = entry
        self.queries: list[tuple[str, str | None]] = []

    def search(
        self,
        _workspace: Path,
        query: str,
        *,
        exclude_session_id: str | None = None,
        limit: int = 5,
    ) -> tuple[RecallEntry, ...]:
        self.queries.append((query, exclude_session_id))
        return (self.entry,) if limit else ()


def test_shell_explicit_and_automatic_recall_are_temporary_control_plane(
    tmp_path: Path,
) -> None:
    store, workspace, _other = _store(tmp_path)
    record = store.create_session(workspace, "fake", "model")
    runner = _RecallRunner()
    session = InteractiveSession(
        runner,  # type: ignore[arg-type]
        ConversationHistory("system"),
        record,
        store,
        "fake",
        "model",
        (),
    )
    entry = RecallEntry(
        "aaaaaaaaaaaa", "tool", "test_parser_unicode failed", 3, BASE, 10
    )
    recall = _RecallService(entry)
    commands = iter(
        (
            "/recall Unicode parser",
            "use that result",
            "之前 tokenizer 怎么失败的？",
            "/exit",
        )
    )
    output: list[str] = []

    assert InteractiveShell(
        session,
        store,
        lambda _prompt: next(commands),
        output.append,
        recall_service=recall,  # type: ignore[arg-type]
    ).run() == 0

    assert recall.queries == [
        ("Unicode parser", record.session_id),
        ("之前 tokenizer 怎么失败的？", record.session_id),
    ]
    assert runner.recall_sets == [(entry,), (), (entry,), ()]
    assert all("test_parser_unicode" not in (message.content or "") for message in session.history.messages)
    assert any("test_parser_unicode failed" in line for line in output)


def test_unknown_command_help_lists_recall(tmp_path: Path) -> None:
    store, workspace, _other = _store(tmp_path)
    record = store.create_session(workspace, "fake", "model")
    session = InteractiveSession(
        _RecallRunner(),  # type: ignore[arg-type]
        ConversationHistory("system"),
        record,
        store,
        "fake",
        "model",
        (),
    )
    commands = iter(("/unknown", "/exit"))
    output: list[str] = []

    assert InteractiveShell(
        session, store, lambda _prompt: next(commands), output.append
    ).run() == 0

    assert any("/recall" in line for line in output)

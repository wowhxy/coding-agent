from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.memory import MemoryItem, MemoryMatch, WorkspaceMemoryStore
from coding_agent.memory_candidate import MemoryCandidate, is_safe_candidate
from coding_agent.protocol import Message, ModelTurn, Role
from coding_agent.session import SessionError
from coding_agent.session_store import JsonSessionStore
from coding_agent.summary import SummaryState
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
SECRET = "unit-test-provider-secret"


def _store(tmp_path: Path, ids: tuple[str, ...] = ("11111111", "22222222")) -> WorkspaceMemoryStore:
    iterator = iter(ids)
    return WorkspaceMemoryStore(
        tmp_path / "home", clock=lambda: NOW, id_generator=lambda: next(iterator)
    )


def test_memory_persists_unicode_redacts_key_and_is_workspace_isolated(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = _store(tmp_path)

    item = store.add(first, f"项目使用 UTF-8，key={SECRET}", (SECRET,))

    assert item.text == "项目使用 UTF-8，key=[REDACTED]"
    assert store.list(first) == (item,)
    assert store.list(second) == ()
    assert SECRET not in next((store.root / "memories").glob("*.json")).read_text(encoding="utf-8")


def test_memory_delete_clear_and_limits(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path, tuple(f"{index:08x}" for index in range(102)))
    first = store.add(workspace, "first", ())
    store.add(workspace, "second", ())

    assert store.delete(workspace, first.id) is True
    assert [item.text for item in store.list(workspace)] == ["second"]
    store.clear(workspace)
    assert store.list(workspace) == ()
    with pytest.raises(SessionError) as too_long:
        store.add(workspace, "x" * 2001, ())
    assert too_long.value.error_code == "MEMORY_INVALID"

    for index in range(100):
        store.add(workspace, str(index), ())
    with pytest.raises(SessionError) as full:
        store.add(workspace, "overflow", ())
    assert full.value.error_code == "MEMORY_LIMIT"


def test_memory_corruption_is_explicit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "valid", ())
    path = next((store.root / "memories").glob("*.json"))
    path.write_text("{", encoding="utf-8")

    with pytest.raises(SessionError) as raised:
        store.list(workspace)
    assert raised.value.error_code == "MEMORY_CORRUPT"


def test_corrupt_memory_has_safe_context_fallback_without_overwriting_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "valid", ())
    path = next((store.root / "memories").glob("*.json"))
    corrupt = b"{not-json"
    path.write_bytes(corrupt)

    assert store.render_for_context(workspace) == ""
    assert path.read_bytes() == corrupt
    with pytest.raises(SessionError) as raised:
        store.list(workspace)
    assert raised.value.error_code == "MEMORY_CORRUPT"


def test_context_priority_is_memory_then_summary_then_recent() -> None:
    history = ConversationHistory("system", "original")
    history.append(Message(Role.ASSISTANT, "old"))
    history.append(Message(Role.USER, "latest"))
    manager = ContextManager(max_context_chars=2_000, recent_turns=1)
    manager.set_workspace_memory("Use pytest; package is src layout")

    context = manager.build(history, summary=SummaryState("earlier work", 1, NOW))

    assert [message.role for message in context[:4]] == [
        Role.SYSTEM,
        Role.USER,
        Role.SYSTEM,
        Role.SYSTEM,
    ]
    assert "Workspace memory" in (context[2].content or "")
    assert "Conversation summary" in (context[3].content or "")
    assert context[-1] == Message(Role.USER, "latest")


def test_render_is_deterministically_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "a" * 2000, ())
    store.add(workspace, "b" * 2000, ())

    rendered = store.render(workspace, max_chars=200)

    assert len(rendered) == 200
    assert "truncated" in rendered


def test_long_memory_selects_top_twelve_by_latest_user_relevance() -> None:
    history = ConversationHistory("system", "original task")
    history.append(Message(Role.ASSISTANT, "old answer"))
    history.append(Message(Role.USER, "please run python pytest tests"))
    manager = ContextManager(max_context_chars=8_000, recent_turns=2)
    entries = [f"[{index:08x}] general convention {index}" for index in range(12)]
    entries.extend(
        (
            "[0000000c] use python for scripts",
            "[0000000d] run pytest for tests",
        )
    )
    manager.set_workspace_memory("\n".join(entries))

    first = manager.build(history)
    second = manager.build(history)
    memory = first[2].content or ""

    assert first == second
    assert sum(line.startswith("[") for line in memory.splitlines()) == 12
    assert "use python for scripts" in memory
    assert "run pytest for tests" in memory
    assert "general convention 10" not in memory
    assert "general convention 11" not in memory


def test_long_memory_uses_original_task_for_one_shot_relevance() -> None:
    history = ConversationHistory("system", "diagnose cmake compiler failure")
    manager = ContextManager(max_context_chars=8_000, recent_turns=1)
    entries = [f"[{index:08x}] ordinary fact {index}" for index in range(13)]
    entries.append("[0000000d] build with cmake compiler")
    manager.set_workspace_memory("\n".join(entries))

    memory = manager.build(history)[2].content or ""

    assert "build with cmake compiler" in memory
    assert "ordinary fact 12" not in memory


def test_switching_session_keeps_workspace_memory_but_not_session_context(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: NOW,
        id_generator=iter(("aaaaaaaaaaaa", "bbbbbbbbbbbb")).__next__,
    )
    first = session_store.save(
        session_store.create_session(workspace, "p", "m")
    )
    second = session_store.create_session(workspace, "p", "m")
    model = FakeModelClient([ModelTurn("done")])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    runner.set_workspace_memory("shared build command: pytest")
    session = InteractiveSession(
        runner,
        ConversationHistory("system"),
        first,
        session_store,
        "p",
        "m",
        (),
    )
    session.activate(second)

    session.execute("new session task")

    request = model.calls[0][0]
    assert any("shared build command: pytest" in (item.content or "") for item in request)
    assert all("old session" not in (item.content or "") for item in request)


def test_shell_memory_commands_update_future_agent_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_store = JsonSessionStore(
        tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "aaaaaaaaaaaa"
    )
    memory_store = _store(tmp_path)
    model = FakeModelClient([ModelTurn("done")])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    session = InteractiveSession(
        runner,
        ConversationHistory("system"),
        session_store.create_session(workspace, "p", "m"),
        session_store,
        "p",
        "m",
        (SECRET,),
    )
    commands = iter((f"/memory add use pytest {SECRET}", "/memory", "task", "/exit"))
    output: list[str] = []

    assert InteractiveShell(
        session,
        session_store,
        lambda _prompt: next(commands),
        output.append,
        memory_store,
    ).run() == 0

    request = model.calls[0][0]
    assert any("use pytest [REDACTED]" in (message.content or "") for message in request)
    assert all(SECRET not in (message.content or "") for message in request)
    assert any("11111111" in line for line in output)


def test_v1_memory_migrates_in_memory_and_next_write_persists_v2_metadata(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "placeholder", ())
    path = next((store.root / "memories").glob("*.json"))
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspace": str(workspace.resolve()),
                "items": [
                    {
                        "id": "11111111",
                        "text": "Use pytest",
                        "created_at": "2026-08-28T10:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = store.list(workspace)
    store.add(workspace, "Python is 3.11", (), kind="architecture")
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated == (
        MemoryItem("11111111", "Use pytest", NOW, "fact", "user", NOW),
    )
    assert persisted["schema_version"] == 2
    assert persisted["items"][0] == {
        "id": "11111111",
        "text": "Use pytest",
        "kind": "fact",
        "source": "user",
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:00:00Z",
    }
    assert persisted["items"][1]["kind"] == "architecture"


def test_memory_classifies_normalized_duplicate_and_explicit_topic_conflict(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    original = store.add(
        workspace,
        "Test runner: pytest",
        (),
        kind="command",
        source="confirmed_candidate",
    )

    duplicate = store.match(workspace, " test RUNNER:   PYTEST ", "command")
    conflict = store.match(workspace, "Test runner: unittest", "command")
    fresh = store.match(workspace, "Python version: 3.11", "architecture")

    assert duplicate == MemoryMatch("duplicate", original)
    assert conflict == MemoryMatch("conflict", original)
    assert fresh == MemoryMatch("new", None)


def test_confirmed_conflict_replace_preserves_identity_and_creation_time(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    times = iter((NOW, datetime(2026, 8, 29, 11, 0, tzinfo=timezone.utc)))
    store = WorkspaceMemoryStore(
        tmp_path / "home",
        clock=lambda: next(times),
        id_generator=lambda: "11111111",
    )
    original = store.add(workspace, "Python version: 3.11", (), kind="architecture")

    replaced = store.replace(
        workspace,
        original.id,
        "Python version: 3.12",
        (),
        kind="architecture",
        source="confirmed_candidate",
    )

    assert replaced.id == original.id
    assert replaced.created_at == original.created_at
    assert replaced.updated_at > original.updated_at
    assert store.list(workspace) == (replaced,)


@pytest.mark.parametrize(
    "text",
    (
        "API_KEY=" + "synthetic-value",
        "sk-" + ("x" * 20),
        "Authorization: Bearer abcdefghijklmnop",
        "password: hunter2-secret",
        "token=abcdefghijklmno",
        "```python\nprint('large source dump')\n```",
    ),
)
def test_candidate_safety_rejects_credentials_and_source_dumps(text: str) -> None:
    candidate = MemoryCandidate(text, "fact", "observed")

    assert is_safe_candidate(candidate, ("current-live-key",)) is False


def test_candidate_safety_rejects_current_key_but_allows_security_policy() -> None:
    assert is_safe_candidate(
        MemoryCandidate("provider key is current-live-key", "fact", "observed"),
        ("current-live-key",),
    ) is False
    assert is_safe_candidate(
        MemoryCandidate("Never commit API keys", "constraint", "user"),
        ("current-live-key",),
    ) is True

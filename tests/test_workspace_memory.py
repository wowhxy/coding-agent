from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.memory import WorkspaceMemoryStore
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


def test_context_priority_is_memory_then_summary_then_recent() -> None:
    history = ConversationHistory("system", "original")
    history.append(Message(Role.ASSISTANT, "old"))
    history.append(Message(Role.USER, "latest"))
    manager = ContextManager(max_context_chars=2_000, recent_turns=1)
    manager.set_workspace_memory("Use pytest; package is src layout")

    context = manager.build(history, summary=SummaryState("earlier work", 1))

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

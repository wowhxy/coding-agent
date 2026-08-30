from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.memory_candidate import (
    MemoryCandidate,
    MemoryCandidateExtractor,
    MemoryEvidence,
)
from coding_agent.memory_policy import (
    MemoryAction,
    MemoryAutoManager,
    MemoryPolicy,
)
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall, ToolResult
from coding_agent.session_store import JsonSessionStore
from fakes import FakeModelClient


NOW = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)


class _Extractor:
    def __init__(self, *candidates: MemoryCandidate) -> None:
        self.candidates = candidates
        self.calls = 0
        self.last_diagnostic: str | None = None

    def extract(self, _messages: tuple[Message, ...]) -> tuple[MemoryCandidate, ...]:
        self.calls += 1
        return self.candidates


def _success(tool_call: ToolCall, output: str) -> tuple[Message, ...]:
    return (
        Message(Role.USER, "inspect and verify the project"),
        Message(Role.ASSISTANT, tool_calls=(tool_call,)),
        Message(
            Role.TOOL,
            ToolResult(tool_call.id, tool_call.name, True, output).as_message_content(),
            tool_call_id=tool_call.id,
        ),
        Message(Role.ASSISTANT, "done"),
    )


def _failure(tool_call: ToolCall) -> tuple[Message, ...]:
    return (
        Message(Role.USER, "inspect and verify the project"),
        Message(Role.ASSISTANT, tool_calls=(tool_call,)),
        Message(
            Role.TOOL,
            ToolResult(
                tool_call.id,
                tool_call.name,
                False,
                "1 failed",
                "COMMAND_FAILED",
                "command exited non-zero",
            ).as_message_content(),
            tool_call_id=tool_call.id,
        ),
        Message(Role.ASSISTANT, "not fixed yet"),
    )


def test_policy_accepts_only_current_turn_explicit_user_evidence() -> None:
    quote = "Do not modify vendor/; it is third-party code."
    candidate = MemoryCandidate(
        "constraint.vendor",
        "Do not modify vendor/",
        "constraint",
        "USER_EXPLICIT",
        MemoryEvidence(user_quote=quote),
    )
    policy = MemoryPolicy(())

    accepted = policy.decide(
        candidate,
        (Message(Role.USER, quote), Message(Role.ASSISTANT, "understood")),
        match_status="new",
    )
    fabricated = policy.decide(
        candidate,
        (Message(Role.USER, "fix the parser"), Message(Role.ASSISTANT, "done")),
        match_status="new",
    )

    assert accepted.action is MemoryAction.ADD
    assert fabricated.action is MemoryAction.IGNORE


def test_policy_validates_config_and_command_evidence_against_real_tool_results() -> None:
    read = ToolCall("read-1", "read_file", '{"path":"CMakeLists.txt"}')
    config_candidate = MemoryCandidate(
        "build.system",
        "cmake",
        "architecture",
        "CONFIG_OBSERVED",
        MemoryEvidence(tool_name="read_file", path="CMakeLists.txt", success=True),
    )
    command = ToolCall("run-1", "execute_command", '{"command":"ctest --output-on-failure"}')
    command_candidate = MemoryCandidate(
        "test.command",
        "ctest --output-on-failure",
        "command",
        "TOOL_VERIFIED",
        MemoryEvidence(
            tool_name="execute_command",
            command="ctest --output-on-failure",
            success=True,
        ),
    )
    policy = MemoryPolicy(())

    assert policy.decide(
        config_candidate,
        _success(read, "cmake_minimum_required(VERSION 3.20)"),
        match_status="new",
    ).action is MemoryAction.ADD
    assert policy.decide(
        command_candidate,
        _success(command, "100% tests passed"),
        match_status="new",
    ).action is MemoryAction.ADD
    assert policy.decide(
        command_candidate,
        _failure(command),
        match_status="new",
    ).action is MemoryAction.IGNORE

    fake_path = MemoryCandidate(
        "build.system",
        "cmake",
        "architecture",
        "CONFIG_OBSERVED",
        MemoryEvidence(tool_name="read_file", path="meson.build", success=True),
    )
    assert policy.decide(
        fake_path,
        _success(read, "cmake_minimum_required(VERSION 3.20)"),
        match_status="new",
    ).action is MemoryAction.IGNORE

    one_off = MemoryCandidate(
        "debug.command",
        "python reproduce.py",
        "command",
        "TOOL_VERIFIED",
        MemoryEvidence(
            tool_name="execute_command", command="python reproduce.py", success=True
        ),
    )
    assert policy.decide(
        one_off,
        _success(
            ToolCall(
                "run-2", "execute_command", '{"command":"python reproduce.py"}'
            ),
            "reproduced",
        ),
        match_status="new",
    ).action is MemoryAction.IGNORE


@pytest.mark.parametrize(
    "candidate",
    (
        MemoryCandidate(
            "debug.failure",
            "Temporary parser failure from this run",
            "fact",
            "USER_EXPLICIT",
            MemoryEvidence(user_quote="Temporary parser failure from this run"),
        ),
        MemoryCandidate(
            "project.guess",
            "probably uses Bazel",
            "architecture",
            "MODEL_INFERRED",
            MemoryEvidence(),
        ),
        MemoryCandidate(
            "provider.token",
            "token=abcdefghijklmnop",
            "fact",
            "USER_EXPLICIT",
            MemoryEvidence(user_quote="token=abcdefghijklmnop"),
        ),
        MemoryCandidate(
            "project.dump",
            "x" * 501,
            "fact",
            "USER_EXPLICIT",
            MemoryEvidence(user_quote="x" * 501),
        ),
    ),
)
def test_policy_ignores_transient_inferred_secret_and_oversized_candidates(
    candidate: MemoryCandidate,
) -> None:
    messages = (Message(Role.USER, candidate.evidence.user_quote or "inspect"),)
    assert MemoryPolicy(()).decide(
        candidate, messages, match_status="new"
    ).action is MemoryAction.IGNORE


def test_auto_manager_adds_updates_and_preserves_explicit_constraint_precedence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    times = iter((NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)))
    ids = iter(("11111111", "22222222"))
    store = WorkspaceMemoryStore(
        tmp_path / "home",
        clock=lambda: next(times),
        id_generator=lambda: next(ids),
    )
    quote = "Always run ctest for this project."
    initial = MemoryCandidate(
        "test.command",
        "ctest",
        "command",
        "USER_EXPLICIT",
        MemoryEvidence(user_quote=quote),
    )
    first = MemoryAutoManager(_Extractor(initial), store, ()).process(
        workspace, (Message(Role.USER, quote),)
    )
    duplicate = MemoryAutoManager(_Extractor(initial), store, ()).process(
        workspace, (Message(Role.USER, quote),)
    )
    update_quote = "From now on always run ctest --output-on-failure."
    update = MemoryCandidate(
        "test.command",
        "ctest --output-on-failure",
        "command",
        "USER_EXPLICIT",
        MemoryEvidence(user_quote=update_quote),
    )
    updated = MemoryAutoManager(_Extractor(update), store, ()).process(
        workspace, (Message(Role.USER, update_quote),)
    )

    assert [change.action for change in first.changes] == [MemoryAction.ADD]
    assert duplicate.changes == ()
    assert [change.action for change in updated.changes] == [MemoryAction.UPDATE]
    items = store.list(workspace)
    assert len(items) == 1
    assert items[0].id == first.changes[0].item.id
    assert items[0].created_at == first.changes[0].item.created_at
    assert items[0].content == "ctest --output-on-failure"

    weaker = MemoryCandidate(
        "test.command",
        "pytest",
        "command",
        "TOOL_VERIFIED",
        MemoryEvidence(
            tool_name="execute_command", command="pytest", success=True
        ),
    )
    ignored = MemoryAutoManager(_Extractor(weaker), store, ()).process(
        workspace,
        _success(ToolCall("run-2", "execute_command", '{"command":"pytest"}'), "ok"),
    )
    assert ignored.changes == ()
    assert store.list(workspace)[0].content == "ctest --output-on-failure"


def test_auto_manager_is_best_effort_and_workspace_isolated(tmp_path: Path) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    store = WorkspaceMemoryStore(
        tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "11111111"
    )
    quote = "The project must use Python 3.11."
    candidate = MemoryCandidate(
        "python.version",
        "Python 3.11",
        "architecture",
        "USER_EXPLICIT",
        MemoryEvidence(user_quote=quote),
    )
    report = MemoryAutoManager(_Extractor(candidate), store, ()).process(
        first_workspace, (Message(Role.USER, quote),)
    )

    assert len(report.changes) == 1
    assert store.list(first_workspace)[0].content == "Python 3.11"
    assert store.list(second_workspace) == ()

    failing = MemoryAutoManager(
        type(
            "FailingExtractor",
            (),
            {
                "last_diagnostic": None,
                "extract": lambda self, _messages: (_ for _ in ()).throw(
                    RuntimeError("provider unavailable")
                ),
            },
        )(),
        store,
        (),
    ).process(first_workspace, (Message(Role.USER, quote),))
    assert failing.changes == ()
    assert failing.diagnostic == "memory candidate extraction failed"

    secret_quote = "token=abcdefghijklmnop"
    secret = MemoryCandidate(
        "provider.token",
        secret_quote,
        "fact",
        "USER_EXPLICIT",
        MemoryEvidence(user_quote=secret_quote),
    )
    rejected = MemoryAutoManager(_Extractor(secret), store, ()).process(
        first_workspace, (Message(Role.USER, secret_quote),)
    )
    assert rejected.changes == ()
    assert rejected.ignored == 1
    assert rejected.diagnostic is None


def test_memory_auto_management_offline_restart_new_session_and_context_e2e(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "cpp_project"
    (workspace / "src").mkdir(parents=True)
    home = tmp_path / "home"
    quote = "Do not modify vendor/ because it is third-party code."
    calls = (
        ToolCall("read", "read_file", '{"path":"CMakeLists.txt"}'),
        ToolCall("list", "list_files", '{"path":"."}'),
        ToolCall("test", "execute_command", '{"command":"ctest"}'),
        ToolCall("failed", "execute_command", '{"command":"debug-once"}'),
    )
    messages = (
        Message(Role.USER, quote),
        Message(Role.ASSISTANT, tool_calls=calls),
        Message(
            Role.TOOL,
            ToolResult(
                "read", "read_file", True, "cmake_minimum_required(VERSION 3.20)"
            ).as_message_content(),
            tool_call_id="read",
        ),
        Message(
            Role.TOOL,
            ToolResult(
                "list", "list_files", True, "CMakeLists.txt\nsrc/main.cpp\nvendor/lib.cpp"
            ).as_message_content(),
            tool_call_id="list",
        ),
        Message(
            Role.TOOL,
            ToolResult("test", "execute_command", True, "100% tests passed").as_message_content(),
            tool_call_id="test",
        ),
        Message(
            Role.TOOL,
            ToolResult(
                "failed",
                "execute_command",
                False,
                "temporary parser failure",
                "COMMAND_FAILED",
                "exit 1",
            ).as_message_content(),
            tool_call_id="failed",
        ),
        Message(Role.ASSISTANT, "project inspected and verified"),
    )
    payload = {
        "candidates": [
            {
                "key": "build.system",
                "content": "cmake",
                "kind": "architecture",
                "source": "CONFIG_OBSERVED",
                "evidence": {
                    "tool_name": "read_file",
                    "path": "CMakeLists.txt",
                    "success": True,
                },
            },
            {
                "key": "test.command",
                "content": "ctest",
                "kind": "command",
                "source": "TOOL_VERIFIED",
                "evidence": {
                    "tool_name": "execute_command",
                    "command": "ctest",
                    "success": True,
                },
            },
            {
                "key": "source.root",
                "content": "src/",
                "kind": "architecture",
                "source": "CONFIG_OBSERVED",
                "evidence": {
                    "tool_name": "list_files",
                    "path": ".",
                    "success": True,
                },
            },
            {
                "key": "constraint.vendor",
                "content": "Do not modify vendor/",
                "kind": "constraint",
                "source": "USER_EXPLICIT",
                "evidence": {"user_quote": quote},
            },
            {
                "key": "debug.failure",
                "content": "temporary parser failure",
                "kind": "fact",
                "source": "MODEL_INFERRED",
                "evidence": {},
            },
        ]
    }
    model = FakeModelClient([ModelTurn(json.dumps(payload))])
    store = WorkspaceMemoryStore(
        home,
        clock=lambda: NOW,
        id_generator=iter(("11111111", "22222222", "33333333", "44444444")).__next__,
    )
    report = MemoryAutoManager(
        MemoryCandidateExtractor(model), store, ()
    ).process(workspace, messages)

    assert len(model.calls) == 1
    assert model.calls[0][1] == ()
    assert [change.action for change in report.changes] == [MemoryAction.ADD] * 4
    assert {(item.key, item.content) for item in store.list(workspace)} == {
        ("build.system", "cmake"),
        ("test.command", "ctest"),
        ("source.root", "src/"),
        ("constraint.vendor", "Do not modify vendor/"),
    }

    sessions = JsonSessionStore(
        home, clock=lambda: NOW, id_generator=iter(("aaaaaaaaaaaa", "bbbbbbbbbbbb")).__next__
    )
    session_a = sessions.create_session(workspace, "fake", "fake")
    sessions.save(replace(session_a, messages=messages))
    restarted_store = WorkspaceMemoryStore(home)
    restarted_items = restarted_store.list(workspace)
    assert restarted_items == store.list(workspace)
    assert sessions.load_session(session_a.session_id, workspace).messages == messages

    session_b = sessions.create_session(workspace, "fake", "fake")
    assert session_b.messages == ()
    assert session_b.summary is None
    context = ContextManager()
    context.set_workspace_memories(restarted_store.context_items_for_context(workspace))
    history_b = ConversationHistory("core")
    history_b.append(Message(Role.USER, "continue this C++ project"))
    rendered = "\n".join(message.content or "" for message in context.build(history_b))
    assert "build.system = cmake" in rendered
    assert "test.command = ctest" in rendered
    assert "temporary parser failure" not in rendered

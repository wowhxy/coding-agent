from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.context_policy import ContextPolicy
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.memory_candidate import MemoryCandidate
from coding_agent.model import ModelTransportError
from coding_agent.protocol import (
    Message,
    ModelTurn,
    Role,
    ToolCall,
    ToolResult,
)
from coding_agent.recall import RecallService
from coding_agent.session import SessionError, SummaryState
from coding_agent.session_store import JsonSessionStore
from coding_agent.skills import ActiveSkill, Skill, SkillMetadata
from coding_agent.summary import SummaryManager
from coding_agent.tools import build_default_registry
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake-model",
        api_key="synthetic-test-key",
        api_key_env="SYNTHETIC_TEST_KEY",
        thinking_mode="provider-default",
        sensitive_env_names=frozenset({"SYNTHETIC_TEST_KEY"}),
        max_steps=6,
        max_context_chars=8_000,
        recent_turns=4,
        max_tool_output_chars=120,
        command_timeout=2,
    )


def _tool_turn(
    messages: list[Message],
    user: str,
    call_id: str,
    name: str,
    arguments: dict[str, object],
    result: ToolResult,
) -> None:
    messages.extend(
        (
            Message(Role.USER, user),
            Message(
                Role.ASSISTANT,
                tool_calls=(ToolCall(call_id, name, json.dumps(arguments)),),
            ),
            Message(
                Role.TOOL,
                result.as_message_content(),
                tool_call_id=call_id,
            ),
            Message(Role.ASSISTANT, f"completed {call_id}"),
        )
    )


class _FourCandidates:
    def extract(self, _messages: tuple[Message, ...]):
        return (
            MemoryCandidate("build.system", "cmake", "architecture", "observed"),
            MemoryCandidate("test.command", "ctest", "command", "observed"),
            MemoryCandidate("source.root", "src", "architecture", "observed"),
            MemoryCandidate(
                "constraint.vendor",
                "do not modify vendor/",
                "constraint",
                "user",
            ),
        )


def _render(messages: tuple[Message, ...]) -> str:
    return "\n".join(message.content or "" for message in messages)


def test_context_memory_final_scenarios_a_to_f(tmp_path: Path) -> None:
    cpp_workspace = tmp_path / "cpp_project"
    python_workspace = tmp_path / "python_project"
    (cpp_workspace / "src").mkdir(parents=True)
    python_workspace.mkdir()
    (cpp_workspace / "src" / "main.cpp").write_text(
        "int main() { return 0; }\n", encoding="utf-8"
    )
    home = tmp_path / "agent-home"
    session_ids = iter(
        (
            "111111111111",
            "222222222222",
            "333333333333",
            "444444444444",
        )
    )
    memory_ids = iter(
        ("11111111", "22222222", "33333333", "44444444", "55555555")
    )
    sessions = JsonSessionStore(
        home, clock=lambda: NOW, id_generator=session_ids.__next__
    )
    memories = WorkspaceMemoryStore(
        home, clock=lambda: NOW, id_generator=memory_ids.__next__
    )

    # Scenario A: four candidates require explicit confirmation, then a long
    # canonical session exercises L1-L4 through a real deterministic AgentRunner.
    session_a = sessions.create_session(cpp_workspace, "fake", "fake-model")
    discovery_runner = AgentRunner(
        FakeModelClient([ModelTurn("project inspected")]),
        ToolRegistry(),
        ContextManager(),
    )
    candidate_inputs = iter(("inspect project", "y", "y", "y", "y", "/exit"))
    assert InteractiveShell(
        InteractiveSession(
            discovery_runner,
            ConversationHistory("core"),
            session_a,
            sessions,
            "fake",
            "fake-model",
            (),
        ),
        sessions,
        lambda _prompt: next(candidate_inputs),
        lambda _message: None,
        memories,
        candidate_extractor=_FourCandidates(),  # type: ignore[arg-type]
    ).run() == 0
    assert {(item.key, item.content) for item in memories.list(cpp_workspace)} == {
        ("build.system", "cmake"),
        ("test.command", "ctest"),
        ("source.root", "src"),
        ("constraint.vendor", "do not modify vendor/"),
    }

    session_a = sessions.load_session(session_a.session_id, cpp_workspace)
    long_messages = list(session_a.messages)
    _tool_turn(
        long_messages,
        "inspect config",
        "config-read",
        "read_file",
        {"path": "config.py"},
        ToolResult("config-read", "read_file", True, "config=true"),
    )
    _tool_turn(
        long_messages,
        "edit parser",
        "parser-edit",
        "replace_in_file",
        {"path": "parser.py", "old_text": "a", "new_text": "b"},
        ToolResult("parser-edit", "replace_in_file", True, "edited"),
    )
    _tool_turn(
        long_messages,
        "run tests",
        "pytest-old",
        "execute_command",
        {"command": "pytest -q"},
        ToolResult("pytest-old", "execute_command", True, "all passed"),
    )
    _tool_turn(
        long_messages,
        "run unicode test",
        "unicode-failure",
        "execute_command",
        {"command": "pytest test_parser_unicode -q"},
        ToolResult(
            "unicode-failure",
            "execute_command",
            False,
            "test_parser_unicode failed",
            "COMMAND_FAILED",
            "exit 1",
        ),
    )
    _tool_turn(
        long_messages,
        "read parser before change",
        "parser-old",
        "read_file",
        {"path": "parser.py"},
        ToolResult("parser-old", "read_file", True, "OLD" * 100),
    )
    _tool_turn(
        long_messages,
        "read parser after change",
        "parser-new",
        "read_file",
        {"path": "parser.py"},
        ToolResult("parser-new", "read_file", True, "NEW" * 180),
    )
    session_a = sessions.save(replace(session_a, messages=tuple(long_messages)))
    canonical_before = session_a.messages
    policy = ContextPolicy(
        max_context_chars=8_000,
        max_tool_output_chars=120,
        recent_turns=4,
        minimum_recent_turns=2,
        summary_trigger_chars=100,
    )
    long_context = ContextManager(policy=policy)
    long_context.set_workspace_memories(
        memories.context_items_for_context(cpp_workspace)
    )
    long_model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(ToolCall("list-now", "list_files", '{"path":"."}'),)
            ),
            ModelTurn("long session complete"),
        ]
    )
    summary_model = FakeModelClient(
        [ModelTransportError("synthetic offline retry"), ModelTurn("summary A")]
    )
    long_runner = AgentRunner(
        long_model,
        build_default_registry(_config(cpp_workspace)),
        long_context,
        summary_manager=SummaryManager(
            summary_model,
            threshold_chars=policy.summary_trigger_chars,
            recent_turns=policy.recent_turns,
            max_summary_chars=policy.summary_chars,
            clock=lambda: NOW,
        ),
    )
    history_a = ConversationHistory.from_persisted("core", session_a.messages)
    long_runner.run_turn(history_a, "continue the long investigation")
    first_long_view = _render(long_model.calls[0][0])
    second_long_view = _render(long_model.calls[1][0])
    assert "[Earlier read_file result omitted: parser.py]" in first_long_view
    assert "output truncated" in first_long_view
    assert "Earlier activity:" in first_long_view
    assert "inspected config.py" in first_long_view
    assert "edited parser.py" in first_long_view
    assert "ran pytest" in first_long_view
    assert "Conversation summary" in second_long_view
    assert long_runner.summary_state is not None
    assert history_a.messages[: len(canonical_before) + 1] == (
        (Message(Role.SYSTEM, "core"),) + canonical_before
    )
    assert history_a.messages[len(canonical_before) + 1] == Message(
        Role.USER, "continue the long investigation"
    )
    assert "NEW" * 180 in "\n".join(
        message.content or "" for message in history_a.messages
    )
    session_a = sessions.save(
        replace(
            session_a,
            messages=history_a.persisted_messages,
            summary=long_runner.summary_state,
        )
    )

    # Scenario B: restart restores memory/summary/recent context, and the next
    # summary call receives only the newly-old tail plus the prior summary.
    resumed_record = sessions.load_session(session_a.session_id, cpp_workspace)
    resumed_history = ConversationHistory.from_persisted(
        "core", resumed_record.messages
    )
    resumed_context = ContextManager(policy=policy)
    resumed_context.set_workspace_memories(
        memories.context_items_for_context(cpp_workspace)
    )
    resumed_model = FakeModelClient([ModelTurn("restart complete")])
    resumed_summary_model = FakeModelClient([ModelTurn("summary A incremental")])
    resumed_runner = AgentRunner(
        resumed_model,
        build_default_registry(_config(cpp_workspace)),
        resumed_context,
        summary_manager=SummaryManager(
            resumed_summary_model,
            threshold_chars=100,
            recent_turns=4,
            clock=lambda: NOW,
        ),
    )
    resumed_runner.restore_summary_state(resumed_record.summary)
    resumed_runner.run_turn(resumed_history, "continue after restart")
    incremental_request = resumed_summary_model.calls[0][0][-1].content or ""
    resumed_view = _render(resumed_model.calls[0][0])
    assert "summary A" in incremental_request
    assert "test_parser_unicode failed" in incremental_request
    assert "config.py" not in incremental_request
    assert "summary A incremental" in resumed_view
    assert "build.system = cmake" in resumed_view
    assert resumed_runner.summary_state is not None
    assert (
        resumed_runner.summary_state.covered_message_count
        > resumed_record.summary.covered_message_count  # type: ignore[union-attr]
    )
    session_a = sessions.save(
        replace(
            resumed_record,
            messages=resumed_history.persisted_messages,
            summary=resumed_runner.summary_state,
        )
    )

    # Scenarios C and D: /new gets clean history/summary but shared memory;
    # conservative automatic recall finds Session A and remains temporary.
    session_b_model = FakeModelClient(
        [ModelTurn("new session ready"), ModelTurn("recalled answer")]
    )
    session_b_context = ContextManager(policy=policy)
    session_b_context.set_workspace_memories(
        memories.context_items_for_context(cpp_workspace)
    )
    session_b_runner = AgentRunner(
        session_b_model,
        build_default_registry(_config(cpp_workspace)),
        session_b_context,
    )
    session_b_runner.restore_summary_state(session_a.summary)
    interactive_b = InteractiveSession(
        session_b_runner,
        ConversationHistory.from_persisted("core", session_a.messages),
        session_a,
        sessions,
        "fake",
        "fake-model",
        (),
    )
    memory_before_recall = memories.list(cpp_workspace)
    shell_inputs = iter(
        (
            "/new",
            "start a clean session",
            "上次 Unicode parser 最后失败的是哪个测试？",
            "/exit",
        )
    )
    assert InteractiveShell(
        interactive_b,
        sessions,
        lambda _prompt: next(shell_inputs),
        lambda _message: None,
        memories,
        recall_service=RecallService(sessions),
    ).run() == 0
    session_b = interactive_b.record
    first_b_view = _render(session_b_model.calls[0][0])
    recalled_b_view = _render(session_b_model.calls[1][0])
    assert session_b.session_id != session_a.session_id
    assert session_b.summary is None
    assert "summary A" not in first_b_view
    assert "test_parser_unicode" not in first_b_view
    assert "build.system = cmake" in first_b_view
    assert "test_parser_unicode failed" in recalled_b_view
    assert memories.list(cpp_workspace) == memory_before_recall
    assert all(
        "Recalled history" not in (message.content or "")
        for message in interactive_b.history.messages
    )

    # Scenario E: memory, history, summary, and recall remain workspace-local.
    memories.add(
        python_workspace,
        "python -m pytest -q",
        (),
        key="test.command",
        kind="command",
    )
    python_session = sessions.save(
        replace(
            sessions.create_session(python_workspace, "fake", "fake-model"),
            messages=(
                Message(Role.USER, "python-only-marker"),
                Message(Role.ASSISTANT, "python result"),
            ),
            summary=SummaryState("python summary", 1, NOW),
        )
    )
    recall = RecallService(sessions, fts_enabled=False)
    assert recall.search(cpp_workspace, "python-only-marker") == ()
    assert [item.key for item in memories.list(python_workspace)] == ["test.command"]
    assert "python -m pytest" not in memories.render(cpp_workspace)
    with pytest.raises(SessionError) as cross_workspace:
        sessions.load_session(python_session.session_id, cpp_workspace)
    assert cross_workspace.value.error_code == "SESSION_WORKSPACE_MISMATCH"
    assert sessions.load_session(session_b.session_id, cpp_workspace).summary is None

    # Scenario F: Skill + Memory + restored Summary + Recent Turns coexist with
    # a real local read_file ToolCall and persisted Agent Loop result.
    skill_record = sessions.save(
        replace(
            sessions.create_session(cpp_workspace, "fake", "fake-model"),
            messages=(
                Message(Role.USER, "original skill task"),
                Message(Role.ASSISTANT, "prior result"),
            ),
            summary=SummaryState("skill session summary", 1, NOW),
        )
    )
    skill_context = ContextManager(policy=policy)
    skill_context.set_workspace_memories(
        memories.context_items_for_context(cpp_workspace)
    )
    skill_model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "read-main",
                        "read_file",
                        '{"path":"src/main.cpp"}',
                    ),
                )
            ),
            ModelTurn("skill loop complete"),
        ]
    )
    skill_runner = AgentRunner(
        skill_model,
        build_default_registry(_config(cpp_workspace)),
        skill_context,
    )
    skill_runner.restore_summary_state(skill_record.summary)
    skill_runner.set_active_skills(
        (
            ActiveSkill(
                Skill(
                    SkillMetadata(
                        "cpp-method",
                        "C++ workflow",
                        "workspace",
                        Path("cpp-method/SKILL.md"),
                    ),
                    "Inspect before editing.",
                ),  # type: ignore[arg-type]
                "manual",
            ),
        )
    )
    skill_session = InteractiveSession(
        skill_runner,
        ConversationHistory.from_persisted("core", skill_record.messages),
        skill_record,
        sessions,
        "fake",
        "fake-model",
        (),
    )
    skill_session.execute("inspect the C++ source")
    combined_view = _render(skill_model.calls[0][0])
    tool_view = _render(skill_model.calls[1][0])
    assert "Active Skill: cpp-method" in combined_view
    assert "build.system = cmake" in combined_view
    assert "skill session summary" in combined_view
    assert "inspect the C++ source" in combined_view
    assert "int main()" in tool_view
    persisted_skill = sessions.load_session(skill_record.session_id, cpp_workspace)
    assert any(message.role is Role.TOOL for message in persisted_skill.messages)

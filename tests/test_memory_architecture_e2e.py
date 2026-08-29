from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.memory_candidate import MemoryCandidateExtractor
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.summary import SummaryManager
from coding_agent.tools import build_default_registry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _runner(workspace: Path, model: FakeModelClient, summary_model=None) -> AgentRunner:
    config = RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake-model",
        api_key="synthetic-key",
        api_key_env="SYNTHETIC_API_KEY",
        thinking_mode="provider-default",
        sensitive_env_names=frozenset({"SYNTHETIC_API_KEY"}),
        max_steps=5,
        max_context_chars=8_000,
        recent_turns=1,
        max_tool_output_chars=2_000,
        command_timeout=2,
    )
    return AgentRunner(
        model,
        build_default_registry(config),
        ContextManager(max_context_chars=8_000, recent_turns=1),
        summary_manager=(
            SummaryManager(
                summary_model,
                threshold_chars=1,
                recent_turns=1,
                clock=lambda: NOW,
            )
            if summary_model is not None
            else None
        ),
    )


def test_memory_summary_restart_new_session_and_workspace_isolation_offline(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "cpp-project"
    other_workspace = tmp_path / "other-project"
    workspace.mkdir()
    other_workspace.mkdir()
    (workspace / "main.cpp").write_text(
        "int main() { return 0; }\n", encoding="utf-8"
    )
    home = tmp_path / "agent-home"
    session_ids = iter(("111111111111", "222222222222", "333333333333"))
    sessions = JsonSessionStore(
        home,
        clock=lambda: NOW,
        id_generator=lambda: next(session_ids),
    )
    memories = WorkspaceMemoryStore(
        home,
        clock=lambda: NOW,
        id_generator=lambda: "aaaaaaaa",
    )

    first = sessions.create_session(workspace, "fake", "fake-model")
    first = sessions.save(
        replace(
            first,
            messages=(
                Message(Role.USER, "maintain this C++ project"),
                Message(Role.ASSISTANT, "old analysis " + "x" * 500),
                Message(Role.USER, "previous request"),
                Message(Role.ASSISTANT, "previous result"),
            ),
        )
    )
    agent_model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("read-1", "read_file", '{"path":"main.cpp"}'),
                )
            ),
            ModelTurn("verified C++ source"),
        ]
    )
    runner = _runner(
        workspace,
        agent_model,
        FakeModelClient([ModelTurn("persistent old-work summary")]),
    )
    inputs = iter(("inspect main.cpp", "y", "/exit"))
    shell = InteractiveShell(
        InteractiveSession(
            runner,
            ConversationHistory.from_persisted("system", first.messages),
            first,
            sessions,
            "fake",
            "fake-model",
            (),
        ),
        sessions,
        lambda _prompt: next(inputs),
        lambda _message: None,
        memories,
        candidate_extractor=MemoryCandidateExtractor(
            FakeModelClient(
                [
                    ModelTurn(
                        '{"candidates":[{"text":"Build command: cmake --build build",'
                        '"kind":"command","source":"observed"}]}'
                    )
                ]
            )
        ),
    )

    assert shell.run() == 0
    persisted = sessions.load_session(first.session_id, workspace)
    assert persisted.summary is not None
    assert persisted.summary.text == "persistent old-work summary"
    assert memories.list(workspace)[0].source == "confirmed_candidate"
    assert any(
        message.role is Role.TOOL and "int main()" in (message.content or "")
        for message in agent_model.calls[1][0]
    )

    resumed_model = FakeModelClient([ModelTurn("resume complete")])
    resumed_runner = _runner(workspace, resumed_model)
    resumed_runner.set_workspace_memory(memories.render_for_context(workspace))
    resumed_runner.restore_summary_state(persisted.summary)
    resumed = InteractiveSession(
        resumed_runner,
        ConversationHistory.from_persisted("system", persisted.messages),
        persisted,
        sessions,
        "fake",
        "fake-model",
        (),
    )
    resumed.execute("continue after restart")
    resumed_context = resumed_model.calls[0][0]
    assert any("Build command: cmake" in (message.content or "") for message in resumed_context)
    assert any("persistent old-work summary" in (message.content or "") for message in resumed_context)

    second = sessions.create_session(workspace, "fake", "fake-model")
    second_model = FakeModelClient([ModelTurn("new session complete")])
    second_runner = _runner(workspace, second_model)
    second_runner.set_workspace_memory(memories.render_for_context(workspace))
    InteractiveSession(
        second_runner,
        ConversationHistory("system"),
        second,
        sessions,
        "fake",
        "fake-model",
        (),
    ).execute("new session task")
    second_context = second_model.calls[0][0]
    assert any("Build command: cmake" in (message.content or "") for message in second_context)
    assert all("persistent old-work summary" not in (message.content or "") for message in second_context)
    assert all("inspect main.cpp" not in (message.content or "") for message in second_context)

    third = sessions.create_session(other_workspace, "fake", "fake-model")
    third_model = FakeModelClient([ModelTurn("other workspace complete")])
    third_runner = _runner(other_workspace, third_model)
    third_runner.set_workspace_memory(memories.render_for_context(other_workspace))
    InteractiveSession(
        third_runner,
        ConversationHistory("system"),
        third,
        sessions,
        "fake",
        "fake-model",
        (),
    ).execute("other workspace task")
    assert all(
        "Build command: cmake" not in (message.content or "")
        for message in third_model.calls[0][0]
    )

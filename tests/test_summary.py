from __future__ import annotations

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.model import ModelTransportError
from coding_agent.protocol import Message, ModelTurn, Role, RunStatus, ToolCall
from coding_agent.summary import SummaryManager
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


def _history() -> ConversationHistory:
    history = ConversationHistory("system policy", "original goal")
    history.append(Message(Role.ASSISTANT, "old answer " + "x" * 80))
    history.append(Message(Role.USER, "recent question"))
    history.append(Message(Role.ASSISTANT, "recent answer"))
    return history


def test_summary_preserves_canonical_history_anchors_and_recent_turn() -> None:
    history = _history()
    before = history.messages
    model = FakeModelClient([ModelTurn("old work summary")])
    manager = SummaryManager(model, threshold_chars=1, recent_turns=1)

    state = manager.prepare(history)
    context = ContextManager(max_context_chars=2_000, recent_turns=1).build(
        history, summary=state
    )

    assert history.messages == before
    assert state is not None and state.text == "old work summary"
    assert state.covered_message_count == 1
    assert model.calls[0][1] == ()
    assert context[0:2] == before[0:2]
    assert context[2].role is Role.SYSTEM and "old work summary" in (context[2].content or "")
    assert context[-2:] == before[-2:]


def test_summary_updates_incrementally_using_previous_summary() -> None:
    history = _history()
    model = FakeModelClient([ModelTurn("summary one"), ModelTurn("summary two")])
    manager = SummaryManager(model, threshold_chars=1, recent_turns=1)
    first = manager.prepare(history)
    history.append(Message(Role.USER, "new question"))
    history.append(Message(Role.ASSISTANT, "new answer"))

    second = manager.prepare(history, first)

    assert first is not None and second is not None
    assert second.covered_message_count > first.covered_message_count
    request_text = model.calls[1][0][-1].content or ""
    assert "summary one" in request_text
    assert "recent question" in request_text


def test_summary_failure_returns_prior_state_and_context_falls_back() -> None:
    history = _history()
    model = FakeModelClient([ModelTransportError("offline")])
    manager = SummaryManager(model, threshold_chars=1, recent_turns=1)

    state = manager.prepare(history)
    context = ContextManager(max_context_chars=2_000, recent_turns=1).build(
        history, summary=state
    )

    assert state is None
    assert all("conversation summary" not in (message.content or "").casefold() for message in context)


def test_summary_rejects_tool_call_response_without_affecting_agent_turn() -> None:
    history = _history()
    summary_model = FakeModelClient(
        [ModelTurn(tool_calls=(ToolCall("c1", "read_file", "{}"),))]
    )
    manager = SummaryManager(summary_model, threshold_chars=1, recent_turns=1)
    task_model = FakeModelClient([ModelTurn("normal final")])
    runner = AgentRunner(
        task_model,
        ToolRegistry(),
        ContextManager(max_context_chars=2_000),
        summary_manager=manager,
    )

    result = runner.run_turn(history, "next task")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "normal final"

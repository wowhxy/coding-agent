import pytest

from coding_agent.context import (
    ContextBudgetError,
    ContextManager,
    ConversationHistory,
    truncate_text,
)
from coding_agent.protocol import Message, Role, ToolCall, ToolResult


def test_history_starts_with_permanent_system_and_user_anchors() -> None:
    history = ConversationHistory("system prompt", "original task")

    assert history.messages == (
        Message(Role.SYSTEM, "system prompt"),
        Message(Role.USER, "original task"),
    )


def test_build_keeps_anchors_and_only_recent_complete_turns() -> None:
    history = ConversationHistory("system", "original task")
    history.append(Message(Role.ASSISTANT, tool_calls=(ToolCall("1", "x", "{}"),)))
    history.append(Message(Role.TOOL, "old result", tool_call_id="1"))
    history.append(Message(Role.ASSISTANT, "new final"))

    messages = ContextManager(recent_turns=1).build(history)

    assert [message.content for message in messages] == [
        "system",
        "original task",
        "new final",
    ]


def test_recent_tool_turn_keeps_assistant_call_and_all_results_together() -> None:
    calls = (ToolCall("2", "read_file", "{}"), ToolCall("3", "search_text", "{}"))
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "older final"))
    history.append(Message(Role.ASSISTANT, tool_calls=calls))
    history.append(Message(Role.TOOL, "file", tool_call_id="2"))
    history.append(Message(Role.TOOL, "matches", tool_call_id="3"))

    messages = ContextManager(recent_turns=1).build(history)

    assert messages == (
        Message(Role.SYSTEM, "system"),
        Message(Role.USER, "task"),
        Message(Role.ASSISTANT, tool_calls=calls),
        Message(Role.TOOL, "file", tool_call_id="2"),
        Message(Role.TOOL, "matches", tool_call_id="3"),
    )


def test_budget_removes_the_oldest_whole_turn_before_newer_turns() -> None:
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "A" * 200))
    history.append(Message(Role.TOOL, "B" * 200, tool_call_id="old"))
    history.append(Message(Role.ASSISTANT, "new final"))

    before = history.messages
    messages = ContextManager(max_context_chars=350, recent_turns=2).build(history)

    assert [message.content for message in messages] == ["system", "task", "new final"]
    assert history.messages == before


def test_build_rejects_anchors_that_exceed_the_total_budget() -> None:
    history = ConversationHistory("S" * 200, "T" * 200)

    with pytest.raises(ContextBudgetError, match="system prompt and original user task"):
        ContextManager(max_context_chars=100).build(history)


def test_truncate_text_counts_marker_inside_limit_and_keeps_both_ends() -> None:
    result = truncate_text("A" * 100 + "Z" * 100, 120)

    assert len(result) == 120
    assert result.startswith("A")
    assert result.endswith("Z")
    assert "[output truncated: original=200 chars, kept=120 chars]" in result


def test_truncate_text_returns_short_text_unchanged() -> None:
    assert truncate_text("short output", 120) == "short output"


def test_truncate_text_rejects_nonpositive_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        truncate_text("text", 0)


def test_prepare_tool_result_preserves_identity_and_error_fields() -> None:
    source = ToolResult(
        "1",
        "execute_command",
        False,
        "A" * 100 + "Z" * 100,
        "COMMAND_FAILED",
        "exit 2",
    )

    result = ContextManager(max_tool_output_chars=120).prepare_tool_result(source)

    assert result.tool_call_id == "1"
    assert result.tool_name == "execute_command"
    assert result.ok is False
    assert len(result.output) == 120
    assert result.error_code == "COMMAND_FAILED"
    assert result.error_message == "exit 2"


@pytest.mark.parametrize(
    ("keyword", "values"),
    [
        ("max_context_chars", {"max_context_chars": 0}),
        ("recent_turns", {"recent_turns": 0}),
        ("max_tool_output_chars", {"max_tool_output_chars": 0}),
    ],
)
def test_context_limits_must_be_positive(keyword: str, values: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="context limits must be positive"):
        ContextManager(**values)

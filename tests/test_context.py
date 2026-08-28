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


def test_history_without_original_task_contains_only_system_message() -> None:
    history = ConversationHistory("system prompt")

    assert history.messages == (Message(Role.SYSTEM, "system prompt"),)
    assert history.persisted_messages == ()


def test_history_recovery_uses_current_system_prompt() -> None:
    persisted = (
        Message(Role.USER, "original task"),
        Message(Role.ASSISTANT, "completed"),
    )

    history = ConversationHistory.from_persisted("current system", persisted)

    assert history.messages == (Message(Role.SYSTEM, "current system"),) + persisted
    assert history.persisted_messages == persisted


@pytest.mark.parametrize(
    "persisted",
    [
        (),
        (Message(Role.ASSISTANT, "not a user task"),),
        (Message(Role.SYSTEM, "stale system"), Message(Role.USER, "task")),
        (Message(Role.USER, "task"), Message(Role.SYSTEM, "stale system")),
    ],
)
def test_history_recovery_rejects_invalid_persisted_message_roles(
    persisted: tuple[Message, ...],
) -> None:
    with pytest.raises(ValueError):
        ConversationHistory.from_persisted("current system", persisted)


def test_history_copy_has_independent_mutable_backing_list() -> None:
    history = ConversationHistory("system", "task")
    copied = history.copy()

    copied.append(Message(Role.ASSISTANT, "only copied"))

    assert history.messages == (
        Message(Role.SYSTEM, "system"),
        Message(Role.USER, "task"),
    )
    assert copied.messages[-1] == Message(Role.ASSISTANT, "only copied")


def test_recent_turns_one_keeps_latest_user_led_group_beside_anchors() -> None:
    history = ConversationHistory("system", "original task")
    history.append(Message(Role.ASSISTANT, "first-task final"))
    history.append(Message(Role.USER, "latest request"))
    history.append(Message(Role.ASSISTANT, "latest response"))

    messages = ContextManager(recent_turns=1).build(history)

    assert [message.content for message in messages] == [
        "system",
        "original task",
        "latest request",
        "latest response",
    ]


def test_first_turn_assistant_tool_tail_remains_selectable_beside_anchors() -> None:
    call = ToolCall("first-call", "list_files", '{"path":"."}')
    history = ConversationHistory("system", "original task")
    history.append(Message(Role.ASSISTANT, tool_calls=(call,)))
    history.append(Message(Role.TOOL, "files", tool_call_id="first-call"))

    messages = ContextManager(max_context_chars=1_000, recent_turns=1).build(history)

    assert messages == (
        Message(Role.SYSTEM, "system"),
        Message(Role.USER, "original task"),
        Message(Role.ASSISTANT, tool_calls=(call,)),
        Message(Role.TOOL, "files", tool_call_id="first-call"),
    )


def test_later_user_turn_keeps_tool_call_and_result_batch_together() -> None:
    calls = (ToolCall("2", "read_file", "{}"), ToolCall("3", "search_text", "{}"))
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "first-task final"))
    history.append(Message(Role.USER, "inspect the files"))
    history.append(Message(Role.ASSISTANT, tool_calls=calls))
    history.append(Message(Role.TOOL, "file", tool_call_id="2"))
    history.append(Message(Role.TOOL, "matches", tool_call_id="3"))

    messages = ContextManager(recent_turns=1).build(history)

    assert messages == (
        Message(Role.SYSTEM, "system"),
        Message(Role.USER, "task"),
        Message(Role.USER, "inspect the files"),
        Message(Role.ASSISTANT, tool_calls=calls),
        Message(Role.TOOL, "file", tool_call_id="2"),
        Message(Role.TOOL, "matches", tool_call_id="3"),
    )


def test_budget_removes_oldest_user_led_group_before_newer_groups() -> None:
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "first-task final"))
    history.append(Message(Role.USER, "older request"))
    history.append(Message(Role.ASSISTANT, "A" * 200))
    history.append(Message(Role.USER, "newest request"))
    history.append(Message(Role.ASSISTANT, "newest final"))

    before = history.messages
    messages = ContextManager(max_context_chars=350, recent_turns=2).build(history)

    assert [message.content for message in messages] == [
        "system",
        "task",
        "newest request",
        "newest final",
    ]
    assert history.messages == before


def test_later_users_start_distinct_groups_in_deterministic_order() -> None:
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "first-task final"))
    history.append(Message(Role.USER, "first follow-up"))
    history.append(Message(Role.ASSISTANT, "first follow-up final"))
    history.append(Message(Role.USER, "second follow-up"))
    history.append(Message(Role.ASSISTANT, "second follow-up final"))

    messages = ContextManager(recent_turns=2).build(history)

    assert [message.content for message in messages] == [
        "system",
        "task",
        "first follow-up",
        "first follow-up final",
        "second follow-up",
        "second follow-up final",
    ]


def test_build_rejects_oversized_latest_user_group_instead_of_dropping_it() -> None:
    history = ConversationHistory("system", "task")
    history.append(Message(Role.ASSISTANT, "first-task final"))
    history.append(Message(Role.USER, "latest request" * 30))
    history.append(Message(Role.ASSISTANT, "latest tail" * 30))

    with pytest.raises(ContextBudgetError, match="latest user-led turn"):
        ContextManager(max_context_chars=200, recent_turns=1).build(history)


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

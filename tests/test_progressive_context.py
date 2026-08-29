from __future__ import annotations

import json

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextBudgetError, ContextManager, ConversationHistory
from coding_agent.context_policy import ContextPolicy
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall, ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, ToolRegistry
from fakes import FakeModelClient


def _result(call_id: str, tool: str, output: str, *, ok: bool = True) -> Message:
    result = (
        ToolResult(call_id, tool, True, output)
        if ok
        else ToolResult(call_id, tool, False, output, "FAILED", "failed evidence")
    )
    return Message(Role.TOOL, result.as_message_content(), tool_call_id=call_id)


def _append_tool_turn(
    history: ConversationHistory,
    user: str,
    call_id: str,
    tool: str,
    arguments: dict[str, object],
    output: str,
    *,
    ok: bool = True,
) -> None:
    history.append(Message(Role.USER, user))
    history.append(
        Message(
            Role.ASSISTANT,
            tool_calls=(ToolCall(call_id, tool, json.dumps(arguments)),),
        )
    )
    history.append(_result(call_id, tool, output, ok=ok))
    history.append(Message(Role.ASSISTANT, f"finished {call_id}"))


def test_l1_trims_model_view_but_canonical_history_keeps_full_tool_output() -> None:
    output = "A" * 220 + "Z" * 220
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            ToolDefinition("inspect", "inspect", {"type": "object"}),
            lambda arguments: arguments,
            lambda call_id, _arguments: ToolResult(call_id, "inspect", True, output),
        )
    )
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("one", "inspect", "{}"),)),
            ModelTurn("done"),
        ]
    )
    history = ConversationHistory("core")
    runner = AgentRunner(
        model,
        registry,
        ContextManager(policy=ContextPolicy(max_tool_output_chars=120)),
    )

    runner.run_turn(history, "task")

    canonical_payload = json.loads(history.messages[-2].content or "{}")
    model_payload = json.loads(model.calls[1][0][-1].content or "{}")
    assert canonical_payload["output"] == output
    assert len(model_payload["output"]) == 120
    assert "output truncated" in model_payload["output"]


@pytest.mark.parametrize(
    ("tool", "arguments", "label"),
    (
        ("read_file", {"path": "parser.py"}, "parser.py"),
        ("search_text", {"path": "src", "query": "parse"}, "parse"),
        ("list_files", {"path": "src"}, "src"),
    ),
)
def test_l2_prunes_only_superseded_successful_read_search_list_payloads(
    tool: str, arguments: dict[str, object], label: str
) -> None:
    history = ConversationHistory("core", "original")
    _append_tool_turn(history, "first", "old", tool, arguments, "OLD-PAYLOAD")
    _append_tool_turn(history, "second", "new", tool, arguments, "NEW-PAYLOAD")
    before = history.messages
    manager = ContextManager(
        policy=ContextPolicy(recent_turns=3, minimum_recent_turns=1)
    )

    view = manager.build(history)

    old_message = next(message for message in view if message.tool_call_id == "old")
    new_message = next(message for message in view if message.tool_call_id == "new")
    assert f"[Earlier {tool} result omitted: {label}]" in (old_message.content or "")
    assert "OLD-PAYLOAD" not in (old_message.content or "")
    assert "NEW-PAYLOAD" in (new_message.content or "")
    assert history.messages == before
    assert any(
        call.id == "old"
        for message in view
        for call in message.tool_calls
    )


def test_l2_keeps_failed_read_and_command_evidence() -> None:
    history = ConversationHistory("core", "original")
    _append_tool_turn(
        history,
        "failed read",
        "read-old",
        "read_file",
        {"path": "parser.py"},
        "READ-ERROR-EVIDENCE",
        ok=False,
    )
    _append_tool_turn(
        history,
        "new read",
        "read-new",
        "read_file",
        {"path": "parser.py"},
        "NEW-READ",
    )
    _append_tool_turn(
        history,
        "first test",
        "cmd-old",
        "execute_command",
        {"command": "pytest"},
        "OLD-TEST-RESULT",
    )
    _append_tool_turn(
        history,
        "second test",
        "cmd-new",
        "execute_command",
        {"command": "pytest"},
        "NEW-TEST-RESULT",
    )

    view = ContextManager(
        policy=ContextPolicy(recent_turns=5, minimum_recent_turns=2)
    ).build(history)
    rendered = "\n".join(message.content or "" for message in view)

    assert "READ-ERROR-EVIDENCE" in rendered
    assert "OLD-TEST-RESULT" in rendered
    assert "NEW-TEST-RESULT" in rendered


def test_l3_compresses_very_old_explicit_tool_activity_without_inference() -> None:
    history = ConversationHistory("core", "original")
    _append_tool_turn(history, "inspect", "read", "read_file", {"path": "config.py"}, "x" * 200)
    _append_tool_turn(
        history,
        "edit",
        "edit",
        "replace_in_file",
        {"path": "parser.py", "old_text": "a", "new_text": "b"},
        "edited",
    )
    _append_tool_turn(
        history,
        "test",
        "test",
        "execute_command",
        {"command": "pytest -q"},
        "passed",
    )
    _append_tool_turn(history, "latest", "latest", "read_file", {"path": "latest.py"}, "latest")
    before = history.messages
    manager = ContextManager(
        policy=ContextPolicy(
            max_context_chars=2_000,
            recent_turns=1,
            minimum_recent_turns=1,
            summary_trigger_chars=100,
        )
    )

    view = manager.build(history)
    rendered = "\n".join(message.content or "" for message in view)

    assert "Earlier activity:" in rendered
    assert "inspected config.py" in rendered
    assert "edited parser.py" in rendered
    assert "ran pytest" in rendered
    assert "passed" not in rendered
    assert any(
        '"path": "latest.py"' in call.arguments_json
        for message in view
        for call in message.tool_calls
    )
    assert history.messages == before


def test_context_policy_rejects_impossible_minimum_and_context_never_coarse_slices() -> None:
    with pytest.raises(ValueError, match="minimum_recent_turns"):
        ContextPolicy(recent_turns=1, minimum_recent_turns=2)

    history = ConversationHistory("S" * 200, "T" * 200)
    with pytest.raises(ContextBudgetError):
        ContextManager(policy=ContextPolicy(max_context_chars=100)).build(history)
    assert history.messages == (
        Message(Role.SYSTEM, "S" * 200),
        Message(Role.USER, "T" * 200),
    )


def test_total_budget_pressure_can_trigger_summary_before_default_threshold() -> None:
    history = ConversationHistory("core", "original task")
    for index in range(6):
        history.append(Message(Role.ASSISTANT, "old output " + "x" * 180))
        history.append(Message(Role.USER, f"follow-up {index}"))
    manager = ContextManager(
        policy=ContextPolicy(
            max_context_chars=1_000,
            recent_turns=2,
            minimum_recent_turns=1,
            summary_trigger_chars=60_000,
        )
    )

    assert manager.needs_summary(history) is True

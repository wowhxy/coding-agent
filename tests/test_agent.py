from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager
from coding_agent.model import ModelTransportError
from coding_agent.protocol import (
    AgentEvent,
    Message,
    ModelTurn,
    Role,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from coding_agent.tools.registry import RegisteredTool, ToolRegistry, require_keys
from fakes import FakeModelClient


def _register_static_tool(
    registry: ToolRegistry,
    name: str,
    *,
    ok: bool = True,
    output: str = "done",
    error_code: str = "TOOL_FAILED",
    error_message: str = "try another approach",
    on_call: Callable[[str], None] | None = None,
) -> None:
    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required=(), optional=())
        return arguments

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        if on_call is not None:
            on_call(name)
        if ok:
            return ToolResult(call_id, name, True, output)
        return ToolResult(
            call_id,
            name,
            False,
            output,
            error_code,
            error_message,
        )

    registry.register(
        RegisteredTool(
            ToolDefinition(
                name,
                f"Run {name}.",
                {"type": "object", "additionalProperties": False},
            ),
            validate,
            handle,
        )
    )


def test_nonempty_final_is_protocol_level_termination() -> None:
    model = FakeModelClient([ModelTurn("finished", ())])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())

    result = runner.run("system", "task")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "finished"
    assert result.steps == 1
    assert result.error is None


@pytest.mark.parametrize("final_text", [None, "", "   "])
def test_empty_final_without_tool_calls_is_a_model_error(
    final_text: str | None,
) -> None:
    model = FakeModelClient([ModelTurn(final_text, ())])

    result = AgentRunner(model, ToolRegistry(), ContextManager()).run("system", "task")

    assert result.status is RunStatus.MODEL_ERROR
    assert result.final_text is None
    assert result.steps == 1
    assert result.error is not None


def test_tool_result_is_truncated_serialized_and_fed_back_to_model() -> None:
    registry = ToolRegistry()
    _register_static_tool(
        registry,
        "inspect",
        ok=False,
        output="A" * 100 + "Z" * 100,
        error_code="INSPECTION_FAILED",
        error_message="use a narrower query",
    )
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("c1", "inspect", "{}"),)),
            ModelTurn("recovered"),
        ]
    )

    result = AgentRunner(
        model,
        registry,
        ContextManager(max_tool_output_chars=120),
    ).run("system", "task")

    assert result.status is RunStatus.FINAL_RESPONSE
    feedback = model.calls[1][0][-1]
    assert feedback.role is Role.TOOL
    assert feedback.tool_call_id == "c1"
    payload = json.loads(feedback.content or "")
    assert payload == {
        "ok": False,
        "output": payload["output"],
        "error_code": "INSPECTION_FAILED",
        "error_message": "use a narrower query",
    }
    assert len(payload["output"]) == 120
    assert "output truncated" in payload["output"]


def test_unknown_tool_result_allows_model_recovery() -> None:
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("c1", "missing", "{}"),)),
            ModelTurn("used another approach"),
        ]
    )

    result = AgentRunner(model, ToolRegistry(), ContextManager()).run("system", "task")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "used another approach"
    payload = json.loads(model.calls[1][0][-1].content or "")
    assert payload["ok"] is False
    assert payload["error_code"] == "UNKNOWN_TOOL"


def test_tool_calls_take_precedence_over_text_in_the_same_model_turn() -> None:
    registry = ToolRegistry()
    _register_static_tool(registry, "verify")
    model = FakeModelClient(
        [
            ModelTurn("premature", (ToolCall("c1", "verify", "{}"),)),
            ModelTurn("verified completion"),
        ]
    )

    result = AgentRunner(model, registry, ContextManager()).run("system", "task")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "verified completion"
    assert result.steps == 2
    assert len(model.calls) == 2


def test_multiple_tool_calls_execute_and_feed_back_in_response_order() -> None:
    execution_order: list[str] = []
    registry = ToolRegistry()
    _register_static_tool(registry, "first", on_call=execution_order.append)
    _register_static_tool(registry, "second", on_call=execution_order.append)
    model = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("c1", "first", "{}"),
                    ToolCall("c2", "second", "{}"),
                )
            ),
            ModelTurn("finished"),
        ]
    )

    AgentRunner(model, registry, ContextManager()).run("system", "task")

    assert execution_order == ["first", "second"]
    feedback = model.calls[1][0][-2:]
    assert [message.tool_call_id for message in feedback] == ["c1", "c2"]


def test_max_steps_stops_after_exactly_the_configured_model_turns() -> None:
    registry = ToolRegistry()
    _register_static_tool(registry, "continue")
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("c1", "continue", "{}"),)),
            ModelTurn(tool_calls=(ToolCall("c2", "continue", "{}"),)),
            ModelTurn("must not be requested"),
        ]
    )

    result = AgentRunner(
        model,
        registry,
        ContextManager(),
        max_steps=2,
    ).run("system", "task")

    assert result.status is RunStatus.MAX_STEPS
    assert result.final_text is None
    assert result.steps == 2
    assert len(model.calls) == 2


def test_three_identical_consecutive_failures_return_stalled() -> None:
    registry = ToolRegistry()
    _register_static_tool(registry, "fail", ok=False)
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("c1", "fail", "{}"),)),
            ModelTurn(tool_calls=(ToolCall("c2", "fail", "{}"),)),
            ModelTurn(tool_calls=(ToolCall("c3", "fail", "{}"),)),
            ModelTurn("must not be requested"),
        ]
    )

    result = AgentRunner(model, registry, ContextManager(), max_steps=5).run(
        "system", "task"
    )

    assert result.status is RunStatus.STALLED
    assert result.steps == 3
    assert len(model.calls) == 3


def test_a_different_failure_fingerprint_resets_stall_detection() -> None:
    calls = [
        ToolCall("c1", "missing", "{}"),
        ToolCall("c2", "missing", "{}"),
        ToolCall("c3", "missing", '{"different":true}'),
        ToolCall("c4", "missing", "{}"),
        ToolCall("c5", "missing", "{}"),
    ]
    model = FakeModelClient(
        [ModelTurn(tool_calls=(call,)) for call in calls]
    )

    result = AgentRunner(
        model,
        ToolRegistry(),
        ContextManager(),
        max_steps=5,
    ).run("system", "task")

    assert result.status is RunStatus.MAX_STEPS
    assert len(model.calls) == 5


def test_a_successful_result_resets_stall_detection() -> None:
    outcomes = [False, False, True, False, False]

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required=(), optional=())
        return arguments

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        ok = outcomes.pop(0)
        if ok:
            return ToolResult(call_id, "flaky", True, "worked")
        return ToolResult(call_id, "flaky", False, "", "FAILED", "retry")

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            ToolDefinition("flaky", "Sometimes works.", {"type": "object"}),
            validate,
            handle,
        )
    )
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall(f"c{step}", "flaky", "{}"),))
            for step in range(1, 6)
        ]
    )

    result = AgentRunner(model, registry, ContextManager(), max_steps=5).run(
        "system", "task"
    )

    assert result.status is RunStatus.MAX_STEPS
    assert len(model.calls) == 5


def test_model_client_error_returns_model_error() -> None:
    model = FakeModelClient([ModelTransportError("provider unavailable")])

    result = AgentRunner(model, ToolRegistry(), ContextManager()).run("system", "task")

    assert result.status is RunStatus.MODEL_ERROR
    assert result.steps == 1
    assert result.error == "provider unavailable"


def test_unexpected_local_exception_returns_internal_error_without_traceback() -> None:
    class ExplodingContextManager(ContextManager):
        def build(self, history: object) -> tuple[Message, ...]:
            raise RuntimeError("local failure")

    result = AgentRunner(
        FakeModelClient([]),
        ToolRegistry(),
        ExplodingContextManager(),
    ).run("system", "task")

    assert result.status is RunStatus.INTERNAL_ERROR
    assert result.steps == 1
    assert result.error is not None
    assert "RuntimeError" in result.error
    assert "Traceback" not in result.error


def test_events_report_requested_tool_result_and_final_protocol_status() -> None:
    events: list[AgentEvent] = []
    registry = ToolRegistry()
    _register_static_tool(registry, "inspect")
    model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("c1", "inspect", "{}"),)),
            ModelTurn("finished"),
        ]
    )

    AgentRunner(
        model,
        registry,
        ContextManager(),
        event_sink=events.append,
    ).run("system", "task")

    assert events == [
        AgentEvent("tool_requested", 1, "inspect"),
        AgentEvent("tool_result", 1, "inspect: ok"),
        AgentEvent("run_finished", 2, "FINAL_RESPONSE"),
    ]


def test_fake_model_client_records_snapshots_and_rejects_exhaustion() -> None:
    messages = [Message(Role.USER, "task")]
    definitions = [ToolDefinition("x", "X", {"type": "object"})]
    model = FakeModelClient([ModelTurn("done")])

    assert model.complete(messages, definitions) == ModelTurn("done")
    messages.append(Message(Role.USER, "later"))
    definitions.clear()

    assert model.calls == [
        (
            (Message(Role.USER, "task"),),
            (ToolDefinition("x", "X", {"type": "object"}),),
        )
    ]
    with pytest.raises(AssertionError, match="script exhausted"):
        model.complete((), ())


def test_max_steps_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_steps must be positive"):
        AgentRunner(FakeModelClient([]), ToolRegistry(), ContextManager(), max_steps=0)

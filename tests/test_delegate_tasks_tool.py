from __future__ import annotations

import json

import pytest

from coding_agent.protocol import RunStatus, ToolCall
from coding_agent.subagents.control import create_delegate_tasks_tool
from coding_agent.subagents.models import (
    SubagentContextMode,
    SubagentLimitError,
    SubagentLimits,
    SubagentRequest,
    SubagentResult,
    SubagentRole,
)
from coding_agent.tools.registry import ToolRegistry


class RecordingManager:
    limits = SubagentLimits()

    def __init__(self) -> None:
        self.requests: tuple[SubagentRequest, ...] = ()
        self.failure: Exception | None = None

    def delegate(
        self, requests: tuple[SubagentRequest, ...]
    ) -> tuple[SubagentResult, ...]:
        self.requests = requests
        if self.failure is not None:
            raise self.failure
        return tuple(
            SubagentResult(
                f"subagent-{index}",
                request.role,
                RunStatus.FINAL_RESPONSE,
                f"result {index}",
                index,
                None,
            )
            for index, request in enumerate(requests, start=1)
        )


def _registry(manager: RecordingManager) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(
        (create_delegate_tasks_tool(manager),), source="control:subagent"
    )
    return registry


def _dispatch(manager: RecordingManager, arguments: object):
    return _registry(manager).dispatch(
        ToolCall("delegate-1", "delegate_tasks", json.dumps(arguments))
    )


def test_delegate_tool_has_strict_bounded_schema_and_source() -> None:
    manager = RecordingManager()
    registry = _registry(manager)

    definition = registry.definitions()[0]

    assert definition.name == "delegate_tasks"
    assert registry.source_of("delegate_tasks") == "control:subagent"
    assert definition.input_schema["required"] == ["tasks"]
    assert definition.input_schema["additionalProperties"] is False
    tasks = definition.input_schema["properties"]["tasks"]
    assert tasks["minItems"] == 1
    assert tasks["maxItems"] == 3
    assert tasks["items"]["additionalProperties"] is False
    assert tasks["items"]["properties"]["role"]["enum"] == [
        "explore",
        "analysis",
        "review",
    ]
    assert tasks["items"]["properties"]["context_mode"]["enum"] == [
        "fresh",
        "fork",
    ]


def test_delegate_tool_applies_role_and_context_defaults() -> None:
    manager = RecordingManager()

    result = _dispatch(
        manager,
        {
            "tasks": [
                {"task": "inspect parser"},
                {
                    "task": "review tests",
                    "role": "review",
                    "context_mode": "fork",
                },
            ]
        },
    )

    assert result.ok
    assert manager.requests == (
        SubagentRequest(
            "inspect parser", SubagentRole.EXPLORE, SubagentContextMode.FRESH
        ),
        SubagentRequest(
            "review tests", SubagentRole.REVIEW, SubagentContextMode.FORK
        ),
    )
    assert json.loads(result.output) == {
        "results": [
            {
                "task_id": "subagent-1",
                "role": "explore",
                "status": "FINAL_RESPONSE",
                "result": "result 1",
                "steps": 1,
                "error": None,
            },
            {
                "task_id": "subagent-2",
                "role": "review",
                "status": "FINAL_RESPONSE",
                "result": "result 2",
                "steps": 2,
                "error": None,
            },
        ]
    }


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        {},
        {"tasks": []},
        {"tasks": [{"task": "a"}] * 4},
        {"tasks": ["not an object"]},
        {"tasks": [{}]},
        {"tasks": [{"task": ""}]},
        {"tasks": [{"task": "x", "extra": True}]},
        {"tasks": [{"task": "x", "role": "writer"}]},
        {"tasks": [{"task": "x", "context_mode": "shared"}]},
    ],
)
def test_delegate_tool_rejects_malformed_arguments(arguments: object) -> None:
    result = _dispatch(RecordingManager(), arguments)

    assert not result.ok
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (
            SubagentLimitError("SUBAGENT_LIMIT_REACHED", "run budget exhausted"),
            "SUBAGENT_LIMIT_REACHED",
        ),
        (
            SubagentLimitError("SUBAGENT_DUPLICATE", "duplicate task"),
            "SUBAGENT_DUPLICATE",
        ),
        (RuntimeError("secret traceback"), "SUBAGENT_INTERNAL_ERROR"),
    ],
)
def test_delegate_tool_maps_control_failures_to_stable_codes(
    failure: Exception, code: str
) -> None:
    manager = RecordingManager()
    manager.failure = failure

    result = _dispatch(manager, {"tasks": [{"task": "inspect"}]})

    assert not result.ok
    assert result.error_code == code
    assert "secret traceback" not in (result.error_message or "")


def test_individual_child_failures_remain_successful_control_output() -> None:
    manager = RecordingManager()

    def mixed_delegate(
        requests: tuple[SubagentRequest, ...]
    ) -> tuple[SubagentResult, ...]:
        return (
            SubagentResult(
                "subagent-1",
                SubagentRole.EXPLORE,
                RunStatus.MODEL_ERROR,
                "",
                1,
                "offline",
            ),
        )

    manager.delegate = mixed_delegate  # type: ignore[method-assign]

    result = _dispatch(manager, {"tasks": [{"task": "inspect"}]})

    assert result.ok
    assert json.loads(result.output)["results"][0]["status"] == "MODEL_ERROR"


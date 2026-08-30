from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from coding_agent.application.events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
    adapt_agent_event,
    adapt_subagent_event,
)
from coding_agent.protocol import AgentEvent, RunStatus
from coding_agent.subagents.models import SubagentEvent, SubagentRole


def test_product_event_is_immutable_and_requires_safe_typed_metadata() -> None:
    event = ProductEvent(
        ProductEventKind.NOTICE,
        datetime(2026, 8, 29, tzinfo=timezone.utc),
        "111111111111",
        "task-1",
        0,
        "ready",
        metadata=(("provider", "deepseek"),),
    )

    with pytest.raises(FrozenInstanceError):
        event.title = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="metadata"):
        ProductEvent(
            ProductEventKind.NOTICE,
            event.timestamp,
            None,
            None,
            None,
            "bad",
            metadata=(("provider", 1),),  # type: ignore[arg-type]
        )


def test_agent_tool_events_map_to_started_and_failed_without_leaking_secret() -> None:
    secret = "live-provider-secret"

    started = adapt_agent_event(
        AgentEvent(
            "tool_requested",
            2,
            f"execute_command {secret}",
            "execute_command",
            "builtin",
            "command",
        ),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(secret,),
    )
    failed = adapt_agent_event(
        AgentEvent(
            "tool_result",
            2,
            f"execute_command: error COMMAND_FAILED {secret}",
            "execute_command",
            "builtin",
            "command",
        ),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(secret,),
    )

    assert (started.kind, started.status) == (
        ProductEventKind.TOOL_STARTED,
        ActivityStatus.RUNNING,
    )
    assert (failed.kind, failed.status) == (
        ProductEventKind.TOOL_FINISHED,
        ActivityStatus.FAILED,
    )
    assert secret not in started.title + failed.title
    assert "[REDACTED]" in started.title + failed.title
    assert started.timestamp.tzinfo is not None
    assert started.source is ActivitySource.COMMAND_VERIFICATION
    assert started.tool_name == "execute_command"
    assert started.plugin_name is None


def test_plugin_tool_source_is_structured_by_adapter_not_title_parsing() -> None:
    event = adapt_agent_event(
        AgentEvent(
            "tool_requested",
            1,
            "an intentionally unrelated title",
            "git_diff",
            "plugin:git-readonly",
            "tool",
        ),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(),
    )

    assert event.source is ActivitySource.PLUGIN_TOOL
    assert event.tool_name == "git_diff"
    assert event.plugin_name == "git-readonly"


def test_subagent_lifecycle_maps_running_completed_and_failed() -> None:
    started = adapt_subagent_event(
        SubagentEvent("task_started", "subagent-1", SubagentRole.EXPLORE, None, "inspect"),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(),
    )
    completed = adapt_subagent_event(
        SubagentEvent(
            "task_completed",
            "subagent-1",
            SubagentRole.EXPLORE,
            RunStatus.FINAL_RESPONSE,
            "done",
        ),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(),
    )
    failed = adapt_subagent_event(
        SubagentEvent(
            "task_completed",
            "subagent-2",
            SubagentRole.REVIEW,
            RunStatus.INTERNAL_ERROR,
            "failed",
        ),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(),
    )

    assert (started.kind, started.status) == (
        ProductEventKind.SUBAGENT_STARTED,
        ActivityStatus.RUNNING,
    )
    assert (completed.kind, completed.status) == (
        ProductEventKind.SUBAGENT_FINISHED,
        ActivityStatus.SUCCEEDED,
    )
    assert failed.status is ActivityStatus.FAILED
    assert started.source is ActivitySource.CONTROL_SUBAGENT
    assert started.parent_id == "task-1:subagents"
    assert started.metadata == (
        ("role", "explore"),
        ("subagent_id", "subagent-1"),
    )


def test_unknown_agent_event_remains_an_observable_notice() -> None:
    event = adapt_agent_event(
        AgentEvent("future_event", 1, "safe detail"),
        session_id="111111111111",
        task_id="task-1",
        sensitive_values=(),
    )

    assert event.kind is ProductEventKind.NOTICE
    assert event.metadata == (("core_kind", "future_event"),)


def test_error_event_requires_explicit_error_source_when_projected() -> None:
    event = ProductEvent(
        ProductEventKind.ERROR,
        datetime(2026, 8, 29, tzinfo=timezone.utc),
        "111111111111",
        "task-1",
        None,
        "Provider Error",
        status=ActivityStatus.FAILED,
        source=ActivitySource.ERROR,
    )

    assert event.source is ActivitySource.ERROR

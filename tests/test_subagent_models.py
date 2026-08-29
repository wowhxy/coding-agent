from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from coding_agent.protocol import RunStatus
from coding_agent.subagents.models import (
    SubagentContextMode,
    SubagentLimits,
    SubagentRequest,
    SubagentResult,
    SubagentRole,
    SubagentTask,
)


def test_subagent_models_preserve_small_typed_protocol() -> None:
    request = SubagentRequest(
        "Inspect parser.py", SubagentRole.EXPLORE, SubagentContextMode.FRESH
    )
    task = SubagentTask(
        "subagent-1", request.task, request.role, request.context_mode
    )
    result = SubagentResult(
        task.id, task.role, RunStatus.FINAL_RESPONSE, "found issue", 2, None
    )

    assert request.context_mode is SubagentContextMode.FRESH
    assert result.task_id == "subagent-1"
    assert result.status is RunStatus.FINAL_RESPONSE
    with pytest.raises(FrozenInstanceError):
        task.task = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: SubagentRequest("", SubagentRole.EXPLORE, SubagentContextMode.FRESH),
        lambda: SubagentRequest("x" * 2_001, SubagentRole.EXPLORE, SubagentContextMode.FRESH),
        lambda: SubagentRequest("task", "explore", SubagentContextMode.FRESH),
        lambda: SubagentRequest("task", SubagentRole.EXPLORE, "fresh"),
        lambda: SubagentTask("bad id", "task", SubagentRole.EXPLORE, SubagentContextMode.FRESH),
        lambda: SubagentResult("bad id", SubagentRole.REVIEW, RunStatus.MODEL_ERROR, "", 0, "failed"),
    ],
)
def test_subagent_models_reject_invalid_or_unbounded_values(constructor) -> None:
    with pytest.raises((TypeError, ValueError)):
        constructor()


@pytest.mark.parametrize(
    "changes",
    [
        {"max_parallel_subagents": 0},
        {"max_subagent_tasks_per_batch": 4, "max_subagents_per_parent_run": 3},
        {"max_subagent_steps": True},
        {"max_delegation_depth": 2},
        {"max_subagent_result_chars": 6_001, "max_total_subagent_result_chars": 6_000},
        {"max_fork_context_chars": -1},
    ],
)
def test_subagent_limits_reject_invalid_or_inconsistent_values(changes) -> None:
    defaults = {
        "max_parallel_subagents": 3,
        "max_subagent_tasks_per_batch": 3,
        "max_subagents_per_parent_run": 6,
        "max_subagent_steps": 8,
        "max_delegation_depth": 1,
        "max_subagent_result_chars": 6_000,
        "max_total_subagent_result_chars": 16_000,
        "max_fork_context_chars": 12_000,
    }
    defaults.update(changes)

    with pytest.raises(ValueError):
        SubagentLimits(**defaults)


def test_subagent_limit_defaults_are_the_approved_hard_bounds() -> None:
    limits = SubagentLimits()

    assert limits == SubagentLimits(3, 3, 6, 8, 1, 6_000, 16_000, 12_000)

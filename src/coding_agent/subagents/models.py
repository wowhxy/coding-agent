"""Small immutable protocol models and hard limits for Subagents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..protocol import RunStatus


_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
MAX_DELEGATED_TASK_CHARS = 2_000


class SubagentLimitError(ValueError):
    """Stable delegation-policy error consumed by the parent control tool."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SubagentRole(str, Enum):
    EXPLORE = "explore"
    ANALYSIS = "analysis"
    REVIEW = "review"


class SubagentContextMode(str, Enum):
    FRESH = "fresh"
    FORK = "fork"


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    task: str
    role: SubagentRole = SubagentRole.EXPLORE
    context_mode: SubagentContextMode = SubagentContextMode.FRESH

    def __post_init__(self) -> None:
        _validate_task(self.task)
        if not isinstance(self.role, SubagentRole):
            raise TypeError("subagent role is invalid")
        if not isinstance(self.context_mode, SubagentContextMode):
            raise TypeError("subagent context mode is invalid")


@dataclass(frozen=True, slots=True)
class SubagentTask:
    id: str
    task: str
    role: SubagentRole
    context_mode: SubagentContextMode

    def __post_init__(self) -> None:
        _validate_task_id(self.id)
        _validate_task(self.task)
        if not isinstance(self.role, SubagentRole):
            raise TypeError("subagent role is invalid")
        if not isinstance(self.context_mode, SubagentContextMode):
            raise TypeError("subagent context mode is invalid")


@dataclass(frozen=True, slots=True)
class SubagentResult:
    task_id: str
    role: SubagentRole
    status: RunStatus
    result: str
    steps: int
    error: str | None

    def __post_init__(self) -> None:
        _validate_task_id(self.task_id)
        if not isinstance(self.role, SubagentRole):
            raise TypeError("subagent role is invalid")
        if not isinstance(self.status, RunStatus):
            raise TypeError("subagent status is invalid")
        if type(self.result) is not str:
            raise TypeError("subagent result must be text")
        if type(self.steps) is not int or self.steps < 0:
            raise ValueError("subagent steps must be a non-negative integer")
        if self.error is not None and type(self.error) is not str:
            raise TypeError("subagent error must be text or None")


@dataclass(frozen=True, slots=True)
class SubagentEvent:
    """Safe parent-thread lifecycle metadata for CLI observability."""

    kind: str
    task_id: str | None
    role: SubagentRole | None
    status: RunStatus | None
    message: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind:
            raise ValueError("subagent event kind is invalid")
        if self.task_id is not None:
            _validate_task_id(self.task_id)
        if self.role is not None and not isinstance(self.role, SubagentRole):
            raise TypeError("subagent event role is invalid")
        if self.status is not None and not isinstance(self.status, RunStatus):
            raise TypeError("subagent event status is invalid")
        if type(self.message) is not str:
            raise TypeError("subagent event message must be text")


@dataclass(frozen=True, slots=True)
class SubagentLimits:
    max_parallel_subagents: int = 3
    max_subagent_tasks_per_batch: int = 3
    max_subagents_per_parent_run: int = 6
    max_subagent_steps: int = 8
    max_delegation_depth: int = 1
    max_subagent_result_chars: int = 6_000
    max_total_subagent_result_chars: int = 16_000
    max_fork_context_chars: int = 12_000

    def __post_init__(self) -> None:
        values = (
            self.max_parallel_subagents,
            self.max_subagent_tasks_per_batch,
            self.max_subagents_per_parent_run,
            self.max_subagent_steps,
            self.max_delegation_depth,
            self.max_subagent_result_chars,
            self.max_total_subagent_result_chars,
            self.max_fork_context_chars,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("subagent limits must be positive integers")
        if self.max_parallel_subagents > self.max_subagent_tasks_per_batch:
            raise ValueError("parallel limit cannot exceed batch task limit")
        if self.max_subagent_tasks_per_batch > self.max_subagents_per_parent_run:
            raise ValueError("batch task limit cannot exceed parent run limit")
        if self.max_delegation_depth != 1:
            raise ValueError("Subagent v1 delegation depth must be one")
        if self.max_subagent_result_chars > self.max_total_subagent_result_chars:
            raise ValueError("single result limit cannot exceed total result limit")


def _validate_task_id(value: object) -> None:
    if type(value) is not str or _TASK_ID.fullmatch(value) is None:
        raise ValueError("subagent task id is invalid")


def _validate_task(value: object) -> None:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > MAX_DELEGATED_TASK_CHARS
    ):
        raise ValueError(
            f"delegated task must contain 1 to {MAX_DELEGATED_TASK_CHARS} characters"
        )

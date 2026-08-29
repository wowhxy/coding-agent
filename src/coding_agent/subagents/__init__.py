"""Parallel read-only child orchestration for the coding agent."""

from .models import (
    SubagentContextMode,
    SubagentEvent,
    SubagentLimitError,
    SubagentLimits,
    SubagentRequest,
    SubagentResult,
    SubagentRole,
    SubagentTask,
)
from .manager import SubagentManager
from .control import create_delegate_tasks_tool

__all__ = [
    "SubagentContextMode",
    "SubagentEvent",
    "SubagentLimits",
    "SubagentLimitError",
    "SubagentManager",
    "create_delegate_tasks_tool",
    "SubagentRequest",
    "SubagentResult",
    "SubagentRole",
    "SubagentTask",
]

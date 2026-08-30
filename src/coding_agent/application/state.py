"""Immutable product views exposed by the application facade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .events import ActivitySource, ActivityStatus


class AgentState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    ERROR = "error"
    CLOSED = "closed"


class ConversationKind(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SUBAGENT = "subagent"
    ERROR = "error"
    SYSTEM = "system"
    CHANGE = "change"
    VERIFICATION = "verification"


class ChangeStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    name: str | None
    updated_at: datetime
    active: bool
    running: bool
    result_status: str | None

    @property
    def display_name(self) -> str:
        return self.name or "Untitled"


@dataclass(frozen=True, slots=True)
class ConversationItem:
    id: str
    kind: ConversationKind
    content: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ActivityView:
    id: str
    kind: str
    title: str
    detail: str
    status: ActivityStatus
    step: int | None
    expandable: bool
    source: ActivitySource = ActivitySource.BUILTIN_TOOL
    tool_name: str | None = None
    plugin_name: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeView:
    path: str
    status: ChangeStatus
    additions: int
    deletions: int
    diff: str


@dataclass(frozen=True, slots=True)
class VerificationView:
    command: str
    ok: bool
    summary: str
    detail: str


@dataclass(frozen=True, slots=True)
class MemoryView:
    id: str
    kind: str
    key: str
    content: str
    source: str


@dataclass(frozen=True, slots=True)
class SkillView:
    name: str
    description: str
    scope: str
    activation: str


@dataclass(frozen=True, slots=True)
class PluginView:
    name: str
    version: str
    description: str
    status: str
    enabled: bool
    trust_warning: str = "Executable plugins run as trusted local code."


@dataclass(frozen=True, slots=True)
class RecallView:
    session_id: str
    source: str
    excerpt: str
    ordinal: int
    timestamp: datetime
    score: int


@dataclass(frozen=True, slots=True)
class ProductStatus:
    provider: str
    model: str
    workspace: Path
    session_id: str
    agent_state: AgentState
    context_chars: int
    context_limit: int
    summary_active: bool
    memory_count: int
    active_skills: tuple[str, ...]
    enabled_plugins: tuple[str, ...]
    active_subagents: int

    def __post_init__(self) -> None:
        if (
            type(self.context_chars) is not int
            or type(self.context_limit) is not int
            or self.context_chars < 0
            or self.context_limit <= 0
            or self.context_chars > self.context_limit
        ):
            raise ValueError("context usage is invalid")
        if type(self.memory_count) is not int or self.memory_count < 0:
            raise ValueError("memory count is invalid")
        if type(self.active_subagents) is not int or self.active_subagents < 0:
            raise ValueError("subagent count is invalid")

    @property
    def context_percent(self) -> int:
        return min(100, round(self.context_chars * 100 / self.context_limit))


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    status: ProductStatus
    sessions: tuple[SessionView, ...] = ()
    conversation: tuple[ConversationItem, ...] = ()
    activities: tuple[ActivityView, ...] = ()
    changes: tuple[ChangeView, ...] = ()
    verifications: tuple[VerificationView, ...] = ()

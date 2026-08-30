"""Immutable, redacted events shared by product front ends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ..protocol import AgentEvent, RunStatus
from ..subagents.models import SubagentEvent


class ProductEventKind(str, Enum):
    TASK_STARTED = "task_started"
    MODEL_WAITING = "model_waiting"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    SUBAGENT_BATCH = "subagent_batch"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_FINISHED = "subagent_finished"
    FINAL_RESPONSE = "final_response"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    SESSION_CHANGED = "session_changed"
    STATE_CHANGED = "state_changed"
    FILE_CHANGES = "file_changes"
    VERIFICATION = "verification"
    MEMORY_CANDIDATE = "memory_candidate"
    RECALL_RESULT = "recall_result"
    ERROR = "error"
    NOTICE = "notice"


class ActivityStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProductEvent:
    """One safe observable product event; never canonical conversation state."""

    kind: ProductEventKind
    timestamp: datetime
    session_id: str | None
    task_id: str | None
    step: int | None
    title: str
    detail: str = ""
    status: ActivityStatus | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProductEventKind):
            raise TypeError("product event kind is invalid")
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("product event timestamp must be timezone-aware")
        if self.session_id is not None and type(self.session_id) is not str:
            raise TypeError("session id must be text or None")
        if self.task_id is not None and type(self.task_id) is not str:
            raise TypeError("task id must be text or None")
        if self.step is not None and (type(self.step) is not int or self.step < 0):
            raise ValueError("event step must be a non-negative integer or None")
        if type(self.title) is not str or type(self.detail) is not str:
            raise TypeError("event title and detail must be text")
        if self.status is not None and not isinstance(self.status, ActivityStatus):
            raise TypeError("event status is invalid")
        if type(self.metadata) is not tuple or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            for pair in self.metadata
        ):
            raise TypeError("event metadata must contain text pairs")


def adapt_agent_event(
    event: AgentEvent,
    *,
    session_id: str,
    task_id: str,
    sensitive_values: tuple[str, ...],
) -> ProductEvent:
    """Map an existing core event without requiring stdout parsing."""

    message = redact_product_text(event.message, sensitive_values)
    kind = ProductEventKind.NOTICE
    status: ActivityStatus | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    if event.kind == "tool_requested":
        kind = ProductEventKind.TOOL_STARTED
        status = ActivityStatus.RUNNING
    elif event.kind == "tool_result":
        kind = ProductEventKind.TOOL_FINISHED
        status = (
            ActivityStatus.FAILED
            if ": error" in event.message.casefold()
            else ActivityStatus.SUCCEEDED
        )
    elif event.kind == "run_finished":
        status_value = _run_status(event.message)
        if status_value is RunStatus.CANCELLED:
            kind = ProductEventKind.TASK_CANCELLED
            status = ActivityStatus.CANCELLED
        elif status_value is RunStatus.FINAL_RESPONSE:
            kind = ProductEventKind.STATE_CHANGED
            status = ActivityStatus.SUCCEEDED
        else:
            kind = ProductEventKind.TASK_FAILED
            status = ActivityStatus.FAILED
    else:
        metadata = (("core_kind", event.kind),)
    return ProductEvent(
        kind,
        _utc_now(),
        session_id,
        task_id,
        event.step,
        message,
        status=status,
        metadata=metadata,
    )


def adapt_subagent_event(
    event: SubagentEvent,
    *,
    session_id: str,
    task_id: str,
    sensitive_values: tuple[str, ...],
) -> ProductEvent:
    """Map read-only child lifecycle metadata to a product event."""

    if event.kind == "task_started":
        kind = ProductEventKind.SUBAGENT_STARTED
        status = ActivityStatus.RUNNING
    elif event.kind == "task_completed":
        kind = ProductEventKind.SUBAGENT_FINISHED
        if event.status is RunStatus.CANCELLED:
            status = ActivityStatus.CANCELLED
        elif event.status is RunStatus.FINAL_RESPONSE:
            status = ActivityStatus.SUCCEEDED
        else:
            status = ActivityStatus.FAILED
    else:
        kind = ProductEventKind.SUBAGENT_BATCH
        status = (
            ActivityStatus.RUNNING
            if event.kind == "batch_started"
            else ActivityStatus.SUCCEEDED
        )
    metadata_items: list[tuple[str, str]] = []
    if event.role is not None:
        metadata_items.append(("role", event.role.value))
    if event.task_id is not None:
        metadata_items.append(("subagent_id", event.task_id))
    metadata = tuple(metadata_items)
    return ProductEvent(
        kind,
        _utc_now(),
        session_id,
        task_id,
        None,
        redact_product_text(event.message, sensitive_values),
        status=status,
        metadata=metadata,
    )


def redact_product_text(text: str, sensitive_values: tuple[str, ...]) -> str:
    """Remove known non-empty sensitive values from display-only text."""

    safe = text
    for value in sensitive_values:
        if type(value) is str and value:
            safe = safe.replace(value, "[REDACTED]")
    return safe


def _run_status(value: str) -> RunStatus | None:
    try:
        return RunStatus(value)
    except ValueError:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

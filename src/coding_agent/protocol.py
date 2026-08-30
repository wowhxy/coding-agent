"""Provider-neutral protocol types used by the coding agent core."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Role(str, Enum):
    """Roles supported by the internal conversation protocol."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model-requested tool invocation with unparsed JSON arguments."""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A provider-neutral model-facing tool description."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    """One message in the canonical in-memory conversation history."""

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A structured local-tool result suitable for model feedback."""

    tool_call_id: str
    tool_name: str
    ok: bool
    output: str = ""
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("successful ToolResult cannot contain error fields")
        if not self.ok and (not self.error_code or not self.error_message):
            raise ValueError("failed ToolResult requires error_code and error_message")

    def as_message_content(self) -> str:
        """Serialize only the fields the model needs for recovery."""

        return json.dumps(
            {
                "ok": self.ok,
                "output": self.output,
                "error_code": self.error_code,
                "error_message": self.error_message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """A normalized model response."""

    final_text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class RunStatus(str, Enum):
    """Protocol-level outcomes for one agent run."""

    FINAL_RESPONSE = "FINAL_RESPONSE"
    MAX_STEPS = "MAX_STEPS"
    STALLED = "STALLED"
    MODEL_ERROR = "MODEL_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class RunResult:
    """The protocol-level outcome returned by AgentRunner."""

    status: RunStatus
    final_text: str | None
    steps: int
    error: str | None
    streamed: bool = False


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A concise observable event emitted during an agent run."""

    kind: str
    step: int
    message: str
    tool_name: str | None = None
    tool_source: str | None = None
    activity_kind: str | None = None
    tool_ok: bool | None = None
    tool_call_id: str | None = None

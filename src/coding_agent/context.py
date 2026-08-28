"""Deterministic context preparation for the agent conversation."""

from __future__ import annotations

import json
from dataclasses import replace

from .protocol import Message, Role, ToolResult


class ContextBudgetError(ValueError):
    """Raised when permanent anchors or a required latest turn exceed the context budget."""


class ConversationHistory:
    """Canonical in-memory history with permanent system and task anchors."""

    def __init__(
        self, system_prompt: str, original_user_task: str | None = None
    ) -> None:
        self._messages = [Message(Role.SYSTEM, system_prompt)]
        if original_user_task is not None:
            self._messages.append(Message(Role.USER, original_user_task))

    @classmethod
    def from_persisted(
        cls, system_prompt: str, messages: tuple[Message, ...]
    ) -> ConversationHistory:
        """Restore persisted non-system messages under the current policy."""

        if not messages:
            raise ValueError("persisted history must not be empty")
        if messages[0].role is not Role.USER:
            raise ValueError("first persisted message must be a user message")
        if any(message.role is Role.SYSTEM for message in messages):
            raise ValueError("persisted history must not contain system messages")

        history = cls(system_prompt)
        history._messages.extend(messages)
        return history

    @property
    def messages(self) -> tuple[Message, ...]:
        """Return an immutable snapshot of the canonical history."""

        return tuple(self._messages)

    @property
    def persisted_messages(self) -> tuple[Message, ...]:
        """Return an immutable snapshot excluding the current system message."""

        return tuple(self._messages[1:])

    def copy(self) -> ConversationHistory:
        """Return a history whose mutable backing list is independent."""

        copied = type(self)(self._messages[0].content or "")
        copied._messages = list(self._messages)
        return copied

    def append(self, message: Message) -> None:
        """Append one message without altering either permanent anchor."""

        self._messages.append(message)


def truncate_text(text: str, limit: int) -> str:
    """Truncate text deterministically while retaining both ends when possible."""

    if limit <= 0:
        raise ValueError("truncate limit must be positive")
    if len(text) <= limit:
        return text

    marker = (
        f"[output truncated: original={len(text)} chars, kept={limit} chars]"
    )
    if len(marker) >= limit:
        return marker[:limit]

    available = limit - len(marker)
    head_length = available // 2
    tail_length = available - head_length
    return text[:head_length] + marker + text[-tail_length:]


class ContextManager:
    """Build bounded model context without mutating canonical history."""

    def __init__(
        self,
        max_context_chars: int = 80_000,
        recent_turns: int = 8,
        max_tool_output_chars: int = 20_000,
    ) -> None:
        if min(max_context_chars, recent_turns, max_tool_output_chars) <= 0:
            raise ValueError("context limits must be positive")
        self.max_context_chars = max_context_chars
        self.recent_turns = recent_turns
        self.max_tool_output_chars = max_tool_output_chars

    def prepare_tool_result(self, result: ToolResult) -> ToolResult:
        """Return a result whose output respects the deterministic tool limit."""

        return replace(
            result,
            output=truncate_text(result.output, self.max_tool_output_chars),
        )

    def build(self, history: ConversationHistory) -> tuple[Message, ...]:
        """Keep permanent anchors and the newest complete turns within budget."""

        messages = history.messages
        anchors = messages[:2]
        if _serialized_size(anchors) > self.max_context_chars:
            raise ContextBudgetError(
                "system prompt and original user task exceed the context budget"
            )

        turns = _group_turns(messages[2:])[-self.recent_turns :]
        if (
            turns
            and _serialized_size(anchors + tuple(turns[-1]))
            > self.max_context_chars
        ):
            raise ContextBudgetError(
                "permanent anchors and latest user-led turn exceed the context budget"
            )
        while turns and _serialized_size(anchors + _flatten(turns)) > self.max_context_chars:
            turns.pop(0)

        return anchors + _flatten(turns)


def _group_turns(messages: tuple[Message, ...]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    for message in messages:
        if message.role is Role.USER or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def _flatten(turns: list[list[Message]]) -> tuple[Message, ...]:
    return tuple(message for turn in turns for message in turn)


def _serialized_size(messages: tuple[Message, ...]) -> int:
    payload = [
        {
            "role": message.role.value,
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments_json": call.arguments_json,
                }
                for call in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
        }
        for message in messages
    ]
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )

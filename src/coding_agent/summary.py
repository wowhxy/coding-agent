"""Best-effort derived summaries for old conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .context import ConversationHistory, truncate_text
from .model import ModelClient
from .protocol import Message, Role


@dataclass(frozen=True, slots=True)
class SummaryState:
    """A derived summary and the number of old messages it covers."""

    text: str
    covered_message_count: int


class SummaryManager:
    """Incrementally summarize old messages without mutating canonical history."""

    def __init__(
        self,
        model_client: ModelClient,
        threshold_chars: int = 60_000,
        recent_turns: int = 8,
        max_summary_chars: int = 8_000,
    ) -> None:
        if min(threshold_chars, recent_turns, max_summary_chars) <= 0:
            raise ValueError("summary limits must be positive")
        self.model_client = model_client
        self.threshold_chars = threshold_chars
        self.recent_turns = recent_turns
        self.max_summary_chars = max_summary_chars

    def prepare(
        self,
        history: ConversationHistory,
        previous: SummaryState | None = None,
    ) -> SummaryState | None:
        """Return an updated summary, falling back safely on any model failure."""

        messages = history.messages
        if _history_size(messages) <= self.threshold_chars:
            return previous
        old_messages = _old_messages(messages[2:], self.recent_turns)
        covered = 0 if previous is None else previous.covered_message_count
        if len(old_messages) <= covered:
            return previous
        new_messages = old_messages[covered:]
        request = _summary_request(previous, new_messages)
        try:
            turn = self.model_client.complete(request, ())
            if turn.tool_calls or not turn.final_text or not turn.final_text.strip():
                return previous
            text = truncate_text(turn.final_text.strip(), self.max_summary_chars)
            return SummaryState(text, len(old_messages))
        except Exception:
            return previous


def _old_messages(messages: tuple[Message, ...], recent_turns: int) -> tuple[Message, ...]:
    turns: list[list[Message]] = []
    for message in messages:
        if message.role is Role.USER or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    old_turns = turns[:-recent_turns]
    return tuple(message for turn in old_turns for message in turn)


def _summary_request(
    previous: SummaryState | None, messages: tuple[Message, ...]
) -> tuple[Message, ...]:
    transcript = json.dumps(
        [
            {
                "role": message.role.value,
                "content": message.content,
                "tool_calls": [
                    {
                        "name": call.name,
                        "arguments_json": call.arguments_json,
                    }
                    for call in message.tool_calls
                ],
            }
            for message in messages
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    prior = "(none)" if previous is None else previous.text
    return (
        Message(
            Role.SYSTEM,
            "Summarize coding work factually and compactly. Preserve decisions, "
            "files changed, commands, results, failures, and unresolved work. "
            "Do not call tools.",
        ),
        Message(
            Role.USER,
            f"Previous summary:\n{prior}\n\nNew transcript:\n{transcript}",
        ),
    )


def _history_size(messages: tuple[Message, ...]) -> int:
    return len(
        json.dumps(
            [
                {
                    "role": message.role.value,
                    "content": message.content,
                    "tool_calls": [
                        (call.id, call.name, call.arguments_json)
                        for call in message.tool_calls
                    ],
                    "tool_call_id": message.tool_call_id,
                }
                for message in messages
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

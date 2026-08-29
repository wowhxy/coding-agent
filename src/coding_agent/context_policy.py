"""Central deterministic character budgets for context construction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    max_context_chars: int = 80_000
    max_tool_output_chars: int = 20_000
    skill_chars: int = 12_000
    memory_chars: int = 8_000
    summary_chars: int = 8_000
    recall_chars: int = 6_000
    recent_turns: int = 8
    minimum_recent_turns: int = 2
    summary_trigger_chars: int = 60_000

    def __post_init__(self) -> None:
        values = (
            self.max_context_chars,
            self.max_tool_output_chars,
            self.skill_chars,
            self.memory_chars,
            self.summary_chars,
            self.recall_chars,
            self.recent_turns,
            self.minimum_recent_turns,
            self.summary_trigger_chars,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("context policy limits must be positive integers")
        if self.minimum_recent_turns > self.recent_turns:
            raise ValueError(
                "minimum_recent_turns cannot exceed recent_turns"
            )

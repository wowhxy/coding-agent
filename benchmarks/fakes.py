"""Deterministic model double shared by offline benchmark scenarios."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from coding_agent.protocol import Message, ModelTurn, ToolDefinition


class FakeModelClient:
    def __init__(
        self, script: Iterable[ModelTurn | Exception], *, delay_seconds: float = 0
    ) -> None:
        self._script = list(script)
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[tuple[Message, ...], tuple[ToolDefinition, ...]]] = []
        self.closed = False

    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.calls.append((tuple(messages), tuple(tool_definitions)))
        if not self._script:
            raise AssertionError("benchmark FakeModelClient script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True

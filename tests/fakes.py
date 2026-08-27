"""Deterministic test doubles for model-facing agent tests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from coding_agent.protocol import Message, ModelTurn, ToolDefinition


class FakeModelClient:
    """Return or raise scripted items while recording immutable requests."""

    def __init__(self, script: Iterable[ModelTurn | Exception]) -> None:
        self._script = list(script)
        self.calls: list[
            tuple[tuple[Message, ...], tuple[ToolDefinition, ...]]
        ] = []

    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        self.calls.append((tuple(messages), tuple(tool_definitions)))
        if not self._script:
            raise AssertionError("FakeModelClient script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

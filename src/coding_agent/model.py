"""Provider-neutral model client boundary and error hierarchy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .protocol import Message, ModelTurn, ToolDefinition


class ModelClientError(Exception):
    """Base class for expected provider and model-protocol failures."""


class ModelTransportError(ModelClientError):
    """Raised when a model request cannot complete successfully."""


class ModelProtocolError(ModelClientError):
    """Raised when a provider response cannot be normalized safely."""


class ModelClient(Protocol):
    """Minimal synchronous interface consumed by AgentRunner."""

    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        """Return one normalized model turn or raise ModelClientError."""

        ...

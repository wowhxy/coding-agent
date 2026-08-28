"""Synchronous interactive-session orchestration and commit control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from .agent import AgentRunner
from .context import ConversationHistory
from .protocol import RunResult, RunStatus
from .session import SessionError, SessionRecord, redact_messages


class SessionStore(Protocol):
    """Persistence boundary used by an interactive session."""

    def save(self, record: SessionRecord) -> SessionRecord:
        """Persist a record and return its current metadata."""


ResultSink = Callable[[RunResult], None]

_COMMIT_STATUSES = frozenset(
    {
        RunStatus.FINAL_RESPONSE,
        RunStatus.MODEL_ERROR,
        RunStatus.MAX_STEPS,
        RunStatus.STALLED,
    }
)


class InteractiveSession:
    """Run user turns transactionally against one committed history."""

    def __init__(
        self,
        runner: AgentRunner,
        history: ConversationHistory,
        record: SessionRecord,
        store: SessionStore,
        provider: str,
        model: str,
        sensitive_values: tuple[str, ...],
        input_reader: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        result_sink: ResultSink | None = None,
    ) -> None:
        self.runner = runner
        self.history = history
        self.record = record
        self.store = store
        self.provider = provider
        self.model = model
        self.sensitive_values = sensitive_values
        self.input_reader = input_reader
        self.output = output
        self.result_sink = result_sink or (lambda _result: None)

    def run(self) -> int:
        """Accept turns until normal exit or a persistence failure."""

        while True:
            try:
                raw = self.input_reader("you> ")
            except (EOFError, KeyboardInterrupt):
                return 0

            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.casefold() == "/exit":
                return 0

            try:
                self.execute(raw)
            except KeyboardInterrupt:
                return 0
            except SessionError as error:
                self.output(
                    f"[error] {error.error_code}: "
                    f"{_redact_text(error.message, self.sensitive_values)}"
                )
                return 7

    def execute(self, text: str) -> RunResult:
        """Run one transactional turn and persist committable outcomes."""

        working = self.history.copy()
        result = self.runner.run_turn(working, text)
        self.result_sink(result)
        if result.status not in _COMMIT_STATUSES:
            return result

        persisted = replace(
            self.record,
            provider=self.provider,
            model=self.model,
            messages=redact_messages(working.persisted_messages, self.sensitive_values),
        )
        saved = self.store.save(persisted)
        self.record = saved
        self.history = working
        return result

    def activate(self, record: SessionRecord) -> None:
        """Switch this transactional runner to another session record."""

        system_prompt = self.history.messages[0].content or ""
        self.record = record
        self.history = (
            ConversationHistory.from_persisted(system_prompt, record.messages)
            if record.messages
            else ConversationHistory(system_prompt)
        )
        reset = getattr(self.runner, "reset_context_state", None)
        if callable(reset):
            reset()


def _redact_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    for sensitive in sensitive_values:
        if type(sensitive) is str and sensitive:
            value = value.replace(sensitive, "[REDACTED]")
    return value

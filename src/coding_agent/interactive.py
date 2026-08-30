"""Synchronous interactive-session orchestration and commit control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import threading
from typing import Protocol

from .agent import AgentRunner
from .context import ConversationHistory
from .protocol import Message, Role, RunResult, RunStatus
from .session import (
    SessionError,
    SessionNameSource,
    SessionRecord,
    redact_messages,
    redact_summary,
)
from .session_title import generate_session_title


class SessionStore(Protocol):
    """Persistence boundary used by an interactive session."""

    def save(self, record: SessionRecord) -> SessionRecord:
        """Persist a record and return its current metadata."""

    def rename_session(self, record: SessionRecord, name: str) -> SessionRecord:
        """Persist a user-supplied display name."""


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
        self._record_lock = threading.RLock()

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

    def execute(
        self,
        text: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> RunResult:
        """Run one transactional turn and persist committable outcomes."""

        working = self.history.copy()
        previous_summary = getattr(self.runner, "summary_state", None)
        try:
            if cancel_check is None:
                result = self.runner.run_turn(working, text)
            else:
                result = self.runner.run_turn(
                    working, text, cancel_check=cancel_check
                )
            self.result_sink(result)
        except BaseException:
            self._restore_summary(previous_summary)
            raise
        if result.status not in _COMMIT_STATUSES:
            self._restore_summary(previous_summary)
            return result

        with self._record_lock:
            name = self.record.name
            name_source = self.record.name_source
            if (
                result.status is RunStatus.FINAL_RESPONSE
                and name_source is None
                and not _has_protocol_completion(self.record.messages)
            ):
                name = generate_session_title(text)
                name_source = SessionNameSource.AUTO
            persisted = replace(
                self.record,
                provider=self.provider,
                model=self.model,
                messages=redact_messages(
                    working.persisted_messages,
                    self.sensitive_values,
                ),
                summary=redact_summary(
                    getattr(self.runner, "summary_state", self.record.summary),
                    self.sensitive_values,
                ),
                name=name,
                name_source=name_source,
            )
            try:
                saved = self.store.save(persisted)
            except BaseException:
                self._restore_summary(previous_summary)
                raise
            self.record = saved
            self.history = working
        return result

    def rename(self, name: str) -> SessionRecord:
        """Persist a manual name without racing a turn commit."""

        with self._record_lock:
            renamed = self.store.rename_session(self.record, name)
            self.record = renamed
            return renamed

    def _restore_summary(self, state: object) -> None:
        restore = getattr(self.runner, "restore_summary_state", None)
        if callable(restore):
            restore(state)

    def activate(self, record: SessionRecord) -> None:
        """Switch this transactional runner to another session record."""

        with self._record_lock:
            system_prompt = self.history.messages[0].content or ""
            self.record = record
            self.history = (
                ConversationHistory.from_persisted(system_prompt, record.messages)
                if record.messages
                else ConversationHistory(system_prompt)
            )
            restore = getattr(self.runner, "restore_summary_state", None)
            if callable(restore):
                restore(record.summary)
            else:
                reset = getattr(self.runner, "reset_context_state", None)
                if callable(reset):
                    reset()


def _redact_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    for sensitive in sensitive_values:
        if type(sensitive) is str and sensitive:
            value = value.replace(sensitive, "[REDACTED]")
    return value


def _has_protocol_completion(messages: tuple[Message, ...]) -> bool:
    return any(
        message.role is Role.ASSISTANT and not message.tool_calls
        for message in messages
    )

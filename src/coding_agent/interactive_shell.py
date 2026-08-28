"""Lightweight interactive command shell around transactional sessions."""

from __future__ import annotations

from collections.abc import Callable

from .interactive import InteractiveSession, _redact_text
from .memory import WorkspaceMemoryStore
from .session import SessionError
from .scheduler import BackgroundScheduler
from .session_store import JsonSessionStore, SessionSummary


class InteractiveShell:
    """Dispatch local slash commands and ordinary agent turns."""

    def __init__(
        self,
        session: InteractiveSession,
        store: JsonSessionStore,
        input_reader: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        memory_store: WorkspaceMemoryStore | None = None,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self.session = session
        self.store = store
        self.input_reader = input_reader
        self.output = output
        self.memory_store = memory_store
        self.scheduler = scheduler

    def run(self) -> int:
        while True:
            try:
                raw = self.input_reader("you> ")
            except (EOFError, KeyboardInterrupt):
                return 0

            stripped = raw.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=1)
            command = parts[0]
            argument = parts[1] if len(parts) == 2 else ""
            normalized = command.casefold()
            if normalized == "/exit" and not argument:
                return 0

            if not normalized.startswith("/"):
                if self.scheduler is not None:
                    if self.scheduler.is_busy(self.session.record.session_id):
                        self.output(
                            "[error] SESSION_BUSY: session already has a running task"
                        )
                        continue
                    self._refresh_active_session()
                try:
                    self.session.execute(raw)
                except KeyboardInterrupt:
                    return 0
                except SessionError as error:
                    self._print_error(error)
                    return 7
                continue

            try:
                if normalized == "/new" and not argument:
                    self._new()
                elif normalized == "/rename":
                    self._require_idle()
                    self._rename(argument)
                elif normalized == "/delete" and not argument:
                    self._require_idle()
                    self._delete()
                elif normalized == "/sessions" and not argument:
                    self._show_sessions(self.store.list_sessions(self.session.record.workspace))
                elif normalized == "/search":
                    self._show_sessions(
                        self.store.search_sessions(self.session.record.workspace, argument)
                    )
                elif normalized == "/use":
                    self._use(argument)
                elif normalized == "/multiline" and not argument:
                    multiline = self._read_multiline()
                    if multiline is not None and multiline.strip():
                        try:
                            self.session.execute(multiline)
                        except SessionError as error:
                            self._print_error(error)
                            return 7
                elif normalized == "/memory":
                    self._memory(argument)
                elif normalized == "/background":
                    self._background(argument)
                elif normalized == "/jobs" and not argument:
                    self._jobs()
                elif normalized == "/cancel":
                    self._cancel_job(argument)
                else:
                    self.output(
                        f"[error] unknown command: {command}; "
                        "use /sessions, /new, /memory, /jobs, or /exit"
                    )
            except KeyboardInterrupt:
                return 0
            except SessionError as error:
                self._print_error(error)

    def _print_error(self, error: SessionError) -> None:
        message = _redact_text(error.message, self.session.sensitive_values)
        self.output(f"[error] {error.error_code}: {message}")

    def _new(self) -> None:
        current = self.session.record
        created = self.store.create_session(
            current.workspace, self.session.provider, self.session.model
        )
        self.session.activate(created)
        self.output(f"[session] created: {created.session_id}")

    def _rename(self, name: str) -> None:
        renamed = self.store.rename_session(self.session.record, name)
        self.session.record = renamed
        self.output(f"[session] renamed: {renamed.name}")

    def _delete(self) -> None:
        current = self.session.record
        try:
            selected = self.store.delete_session(current.session_id, current.workspace)
        except SessionError as error:
            if error.error_code != "SESSION_NOT_FOUND" or current.messages:
                raise
            selected = self.store.load_latest(current.workspace)
        if selected is None:
            selected = self.store.create_session(
                current.workspace, self.session.provider, self.session.model
            )
        self.session.activate(selected)
        self.output(
            f"[session] deleted {current.session_id}; active: {selected.session_id}"
        )

    def _show_sessions(self, summaries: tuple[SessionSummary, ...]) -> None:
        if not summaries:
            self.output("[session] no matching sessions")
            return
        for item in summaries:
            marker = "*" if item.is_latest else " "
            name = item.name or "(unnamed)"
            self.output(
                f"{marker} {item.session_id}  "
                f"{item.updated_at.isoformat()}  {name}"
            )

    def _use(self, session_id: str) -> None:
        selected = self.store.load_session(
            session_id.strip(), self.session.record.workspace
        )
        self.session.activate(selected)
        self.output(f"[session] active: {selected.session_id}")

    def _read_multiline(self) -> str | None:
        lines: list[str] = []
        self.output("[input] multiline: /send to submit, /cancel to discard")
        while True:
            try:
                line = self.input_reader("... ")
            except (EOFError, KeyboardInterrupt):
                self.output("[input] multiline cancelled")
                return None
            command = line.strip().casefold()
            if command == "/cancel":
                self.output("[input] multiline cancelled")
                return None
            if command == "/send":
                return "\n".join(lines)
            lines.append(line)

    def _memory(self, argument: str) -> None:
        if self.memory_store is None:
            raise SessionError("MEMORY_UNAVAILABLE", "workspace memory is unavailable")
        workspace = self.session.record.workspace
        action, _, value = argument.strip().partition(" ")
        normalized = action.casefold()
        if not argument.strip():
            items = self.memory_store.list(workspace)
            if not items:
                self.output("[memory] empty")
            for item in items:
                self.output(f"[memory] {item.id}: {item.text}")
            return
        if normalized == "add":
            item = self.memory_store.add(
                workspace, value, self.session.sensitive_values
            )
            self.output(f"[memory] added: {item.id}")
        elif normalized == "delete":
            deleted = self.memory_store.delete(workspace, value.strip())
            self.output("[memory] deleted" if deleted else "[memory] not found")
        elif normalized == "clear" and not value:
            self.memory_store.clear(workspace)
            self.output("[memory] cleared")
        else:
            raise SessionError(
                "MEMORY_INVALID",
                "use /memory, /memory add <text>, /memory delete <id>, or /memory clear",
            )
        self.session.runner.set_workspace_memory(self.memory_store.render(workspace))

    def _background(self, task: str) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        if not task.strip():
            raise SessionError("JOB_INVALID", "background task is required")
        persisted = self.store.save(self.session.record)
        self.session.record = persisted
        job = self.scheduler.submit(
            persisted, task, self.session.sensitive_values
        )
        self.output(
            f"[job] started: {job.id}; session: {job.session_id}"
        )

    def _jobs(self) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        jobs = self.scheduler.list()
        if not jobs:
            self.output("[job] none")
        for job in jobs:
            detail = f"; {job.error}" if job.error else ""
            self.output(
                f"[job] {job.id}  {job.status.value}  session={job.session_id}{detail}"
            )

    def _cancel_job(self, job_id: str) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        if not self.scheduler.cancel(job_id.strip()):
            raise SessionError("JOB_NOT_FOUND", "running background job was not found")
        self.output(f"[job] cancellation requested: {job_id.strip()}")

    def _require_idle(self) -> None:
        if self.scheduler is not None and self.scheduler.is_busy(
            self.session.record.session_id
        ):
            raise SessionError("SESSION_BUSY", "session already has a running task")

    def _refresh_active_session(self) -> None:
        try:
            current = self.store.load_session(
                self.session.record.session_id, self.session.record.workspace
            )
        except SessionError as error:
            if error.error_code == "SESSION_NOT_FOUND" and not self.session.record.messages:
                return
            raise
        if current != self.session.record:
            self.session.activate(current)

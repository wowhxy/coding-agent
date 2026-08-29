"""Thread-based background wrapper for independent synchronous agent turns."""

from __future__ import annotations

import re
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .agent import AgentRunner
from .context import ConversationHistory
from .protocol import RunResult, RunStatus
from .session import SessionError, SessionRecord, redact_messages, redact_summary
from .session_store import JsonSessionStore
from .skills import SkillDiagnostic
from .system_prompt import SYSTEM_PROMPT


_JOB_ID = re.compile(r"[0-9a-f]{8}")
_COMMIT_STATUSES = frozenset(
    {
        RunStatus.FINAL_RESPONSE,
        RunStatus.MODEL_ERROR,
        RunStatus.MAX_STEPS,
        RunStatus.STALLED,
    }
)


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class BackgroundRuntime:
    runner: AgentRunner
    close: Callable[[], None]
    prepare_task: (
        Callable[[str, tuple[str, ...]], tuple[SkillDiagnostic, ...]] | None
    ) = None


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    id: str
    session_id: str
    task: str
    status: JobStatus
    result: RunResult | None = None
    error: str | None = None


@dataclass(slots=True)
class _JobState:
    snapshot: BackgroundJob
    workspace: Path
    sensitive_values: tuple[str, ...]
    cancel_event: threading.Event
    manual_skill_names: tuple[str, ...] = ()
    enabled_plugin_names: tuple[str, ...] = ()
    future: Future[None] | None = None


class BackgroundScheduler:
    """Schedule isolated agent turns while preventing overlap per session."""

    def __init__(
        self,
        store: JsonSessionStore,
        runtime_factory: Callable[[tuple[str, ...]], BackgroundRuntime],
        id_generator: Callable[[], str] = lambda: secrets.token_hex(4),
        max_workers: int = 2,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self.store = store
        self.runtime_factory = runtime_factory
        self.id_generator = id_generator
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="coding-agent-job"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobState] = {}
        self._closed = False

    def submit(
        self,
        record: SessionRecord,
        task: str,
        sensitive_values: tuple[str, ...],
        manual_skill_names: tuple[str, ...] = (),
        enabled_plugin_names: tuple[str, ...] = (),
    ) -> BackgroundJob:
        if type(task) is not str or not task.strip():
            raise SessionError("JOB_INVALID", "background task is required")
        if type(enabled_plugin_names) is not tuple or any(
            type(name) is not str for name in enabled_plugin_names
        ):
            raise SessionError("JOB_INVALID", "plugin snapshot is invalid")
        with self._lock:
            if self._closed:
                raise SessionError("JOB_UNAVAILABLE", "background scheduler is closed")
            if self.is_busy(record.session_id):
                raise SessionError("SESSION_BUSY", "session already has a running task")
            try:
                job_id = self.id_generator()
            except Exception:
                raise SessionError("JOB_INVALID", "job id generation failed") from None
            if (
                type(job_id) is not str
                or _JOB_ID.fullmatch(job_id) is None
                or job_id in self._jobs
            ):
                raise SessionError("JOB_INVALID", "job id generation failed")
            snapshot = BackgroundJob(
                job_id, record.session_id, task, JobStatus.QUEUED
            )
            state = _JobState(
                snapshot,
                record.workspace,
                sensitive_values,
                threading.Event(),
                manual_skill_names,
                enabled_plugin_names,
            )
            self._jobs[job_id] = state
            state.future = self._executor.submit(self._run, job_id)
            return snapshot

    def list(self) -> tuple[BackgroundJob, ...]:
        with self._lock:
            return tuple(state.snapshot for state in self._jobs.values())

    def is_busy(self, session_id: str) -> bool:
        with self._lock:
            return any(
                state.snapshot.session_id == session_id
                and state.snapshot.status in {JobStatus.QUEUED, JobStatus.RUNNING}
                for state in self._jobs.values()
            )

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None or state.snapshot.status not in {
                JobStatus.QUEUED,
                JobStatus.RUNNING,
            }:
                return False
            state.cancel_event.set()
            if state.future is not None and state.future.cancel():
                state.snapshot = replace(state.snapshot, status=JobStatus.CANCELLED)
            return True

    def wait(self, job_id: str, timeout: float | None = None) -> BackgroundJob:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                raise SessionError("JOB_NOT_FOUND", "background job was not found")
            future = state.future
        assert future is not None
        try:
            future.result(timeout=timeout)
        except CancelledError:
            pass
        except TimeoutError:
            raise SessionError("JOB_TIMEOUT", "background job is still running") from None
        with self._lock:
            return state.snapshot

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for state in self._jobs.values():
                state.cancel_event.set()
                if state.snapshot.status is JobStatus.QUEUED:
                    state.snapshot = replace(
                        state.snapshot, status=JobStatus.CANCELLED
                    )
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        with self._lock:
            state = self._jobs[job_id]
            if state.cancel_event.is_set():
                state.snapshot = replace(state.snapshot, status=JobStatus.CANCELLED)
                return
            state.snapshot = replace(state.snapshot, status=JobStatus.RUNNING)
        runtime: BackgroundRuntime | None = None
        try:
            record = self.store.load_session(
                state.snapshot.session_id, state.workspace
            )
            history = (
                ConversationHistory.from_persisted(SYSTEM_PROMPT, record.messages)
                if record.messages
                else ConversationHistory(SYSTEM_PROMPT)
            )
            runtime = self.runtime_factory(state.enabled_plugin_names)
            restore_summary = getattr(runtime.runner, "restore_summary_state", None)
            if callable(restore_summary):
                restore_summary(record.summary)
            if runtime.prepare_task is not None:
                runtime.prepare_task(
                    state.snapshot.task, state.manual_skill_names
                )
            result = runtime.runner.run_turn(
                history,
                state.snapshot.task,
                cancel_check=state.cancel_event.is_set,
            )
            if result.status in _COMMIT_STATUSES:
                persisted = replace(
                    record,
                    messages=redact_messages(
                        history.persisted_messages, state.sensitive_values
                    ),
                    summary=redact_summary(
                        getattr(runtime.runner, "summary_state", record.summary),
                        state.sensitive_values,
                    ),
                )
                self.store.save(persisted)
            status = (
                JobStatus.CANCELLED
                if result.status is RunStatus.CANCELLED
                else JobStatus.FAILED
                if result.status is RunStatus.INTERNAL_ERROR
                else JobStatus.COMPLETED
            )
            with self._lock:
                state.snapshot = replace(
                    state.snapshot,
                    status=status,
                    result=result,
                    error=result.error if status is JobStatus.FAILED else None,
                )
        except Exception as exc:
            with self._lock:
                state.snapshot = replace(
                    state.snapshot,
                    status=JobStatus.FAILED,
                    error=f"background job failed: {type(exc).__name__}",
                )
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    pass

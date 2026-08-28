from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.protocol import Message, ModelTurn, Role, RunResult, RunStatus
from coding_agent.scheduler import BackgroundRuntime, BackgroundScheduler, JobStatus
from coding_agent.session import SessionError
from coding_agent.session_store import JsonSessionStore
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc)


def _saved(store: JsonSessionStore, workspace: Path, session_id: str):
    record = store.create_session(workspace, "p", "m")
    assert record.session_id == session_id
    return store.save(replace(record, messages=(Message(Role.USER, "original"),)))


def test_background_job_persists_completed_turn_and_closes_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "111111111111")
    record = _saved(store, workspace, "111111111111")
    closed: list[bool] = []

    def runtime() -> BackgroundRuntime:
        runner = AgentRunner(FakeModelClient([ModelTurn("background done")]), ToolRegistry(), ContextManager())
        return BackgroundRuntime(runner, lambda: closed.append(True))

    scheduler = BackgroundScheduler(store, runtime, id_generator=lambda: "aaaaaaaa", max_workers=1)
    try:
        job = scheduler.submit(record, "background task", ())
        completed = scheduler.wait(job.id, timeout=2)
    finally:
        scheduler.shutdown()

    assert completed.status is JobStatus.COMPLETED
    assert completed.result is not None and completed.result.status is RunStatus.FINAL_RESPONSE
    assert store.load_session(record.session_id, workspace).messages[-2:] == (
        Message(Role.USER, "background task"),
        Message(Role.ASSISTANT, "background done"),
    )
    assert closed == [True]


def test_same_session_busy_is_rejected_but_other_session_can_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222"))
    store = JsonSessionStore(tmp_path / "home", clock=lambda: NOW, id_generator=lambda: next(ids))
    first = _saved(store, workspace, "111111111111")
    second = _saved(store, workspace, "222222222222")
    gate = threading.Event()
    started = threading.Event()

    class BlockingRunner:
        def run_turn(self, history, task, cancel_check=None):
            started.set()
            gate.wait(2)
            history.append(Message(Role.USER, task))
            history.append(Message(Role.ASSISTANT, "done"))
            return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)

    scheduler = BackgroundScheduler(
        store,
        lambda: BackgroundRuntime(BlockingRunner(), lambda: None),  # type: ignore[arg-type]
        id_generator=iter(("aaaaaaaa", "bbbbbbbb")).__next__,
        max_workers=2,
    )
    try:
        first_job = scheduler.submit(first, "one", ())
        assert started.wait(1)
        with pytest.raises(SessionError) as busy:
            scheduler.submit(first, "duplicate", ())
        second_job = scheduler.submit(second, "two", ())
        assert scheduler.is_busy(first.session_id)
        gate.set()
        scheduler.wait(first_job.id, 2)
        scheduler.wait(second_job.id, 2)
    finally:
        gate.set()
        scheduler.shutdown()
    assert busy.value.error_code == "SESSION_BUSY"


def test_running_job_cancellation_is_cooperative_and_does_not_commit_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "111111111111")
    record = _saved(store, workspace, "111111111111")
    entered = threading.Event()
    release = threading.Event()

    class BoundaryRunner:
        def run_turn(self, history, task, cancel_check=None):
            history.append(Message(Role.USER, task))
            entered.set()
            release.wait(2)
            assert cancel_check is not None and cancel_check()
            return RunResult(RunStatus.CANCELLED, None, 0, "run cancelled")

    scheduler = BackgroundScheduler(
        store,
        lambda: BackgroundRuntime(BoundaryRunner(), lambda: None),  # type: ignore[arg-type]
        id_generator=lambda: "aaaaaaaa",
        max_workers=1,
    )
    try:
        job = scheduler.submit(record, "discard", ())
        assert entered.wait(1)
        assert scheduler.cancel(job.id) is True
        release.set()
        cancelled = scheduler.wait(job.id, 2)
    finally:
        release.set()
        scheduler.shutdown()

    assert cancelled.status is JobStatus.CANCELLED
    assert store.load_session(record.session_id, workspace).messages == record.messages


def test_agent_runner_honors_cancel_before_model_call() -> None:
    model = FakeModelClient([ModelTurn("must not run")])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    history = ConversationHistory("system")

    result = runner.run_turn(history, "task", cancel_check=lambda: True)

    assert result.status is RunStatus.CANCELLED
    assert model.calls == []


def test_queued_cancellation_and_worker_failure_are_isolated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    store = JsonSessionStore(tmp_path / "home", clock=lambda: NOW, id_generator=lambda: next(ids))
    first = _saved(store, workspace, "111111111111")
    queued = _saved(store, workspace, "222222222222")
    later = _saved(store, workspace, "333333333333")
    gate = threading.Event()
    calls = 0

    class FirstBlockingRunner:
        def run_turn(self, history, task, cancel_check=None):
            gate.wait(2)
            history.append(Message(Role.USER, task))
            history.append(Message(Role.ASSISTANT, "done"))
            return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)

    def runtime() -> BackgroundRuntime:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BackgroundRuntime(FirstBlockingRunner(), lambda: None)  # type: ignore[arg-type]
        if calls == 2:
            raise RuntimeError("isolated failure")
        return BackgroundRuntime(
            AgentRunner(FakeModelClient([ModelTurn("later done")]), ToolRegistry(), ContextManager()),
            lambda: None,
        )

    job_ids = iter(("aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd"))
    scheduler = BackgroundScheduler(
        store, runtime, id_generator=lambda: next(job_ids), max_workers=1
    )
    try:
        first_job = scheduler.submit(first, "first", ())
        queued_job = scheduler.submit(queued, "never commit", ())
        assert scheduler.cancel(queued_job.id) is True
        gate.set()
        scheduler.wait(first_job.id, 2)
        assert scheduler.wait(queued_job.id, 2).status is JobStatus.CANCELLED

        failed_job = scheduler.submit(queued, "factory failure", ())
        assert scheduler.wait(failed_job.id, 2).status is JobStatus.FAILED
        later_job = scheduler.submit(later, "still works", ())
        assert scheduler.wait(later_job.id, 2).status is JobStatus.COMPLETED
    finally:
        gate.set()
        scheduler.shutdown()

    assert store.load_session(queued.session_id, workspace).messages == queued.messages
    assert store.load_session(later.session_id, workspace).messages[-1] == Message(
        Role.ASSISTANT, "later done"
    )

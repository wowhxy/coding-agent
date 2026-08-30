from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import threading

import pytest

import coding_agent.session_store as session_store
from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.protocol import Message, ModelTurn, Role, RunResult, RunStatus, ToolCall
from coding_agent.session import SessionError, SessionNameSource, SessionRecord
from coding_agent.session_store import JsonSessionStore
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)
SECRET = "fake-known-provider-key"


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[SessionRecord] = []

    def save(self, record: SessionRecord) -> SessionRecord:
        self.saved.append(record)
        return replace(record, model=f"{record.model}-saved", updated_at=NOW)

    def rename_session(self, record: SessionRecord, name: str) -> SessionRecord:
        renamed = replace(
            record,
            name=name.strip(),
            name_source=SessionNameSource.MANUAL,
        )
        self.saved.append(renamed)
        return renamed


class ScriptedRunner:
    def __init__(self, statuses: list[RunStatus]) -> None:
        self.statuses = iter(statuses)
        self.calls: list[tuple[ConversationHistory, str]] = []

    def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
        self.calls.append((history, user_message))
        history.append(Message(Role.USER, user_message))
        status = next(self.statuses)
        if status is RunStatus.FINAL_RESPONSE:
            history.append(Message(Role.ASSISTANT, f"answer: {user_message}"))
            return RunResult(status, f"answer: {user_message}", 1, None)
        return RunResult(status, None, 1, "expected outcome")


def inputs(*items: object) -> Callable[[str], str]:
    iterator = iter(items)

    def read(_prompt: str) -> str:
        item = next(iterator)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item

    return read


def record(tmp_path: Path) -> SessionRecord:
    return SessionRecord(
        session_id="012345abcdef",
        workspace=tmp_path.resolve(),
        provider="old-provider",
        model="old-model",
        created_at=NOW,
        updated_at=NOW,
        messages=(),
    )


def session(
    tmp_path: Path,
    runner: object,
    store: object,
    reader: Callable[[str], str],
    *,
    output: list[str] | None = None,
    results: list[RunResult] | None = None,
) -> InteractiveSession:
    return InteractiveSession(
        runner=runner,  # type: ignore[arg-type]
        history=ConversationHistory("system policy"),
        record=record(tmp_path),
        store=store,
        provider="current-provider",
        model="current-model",
        sensitive_values=(SECRET,),
        input_reader=reader,
        output=(output if output is not None else []).append,
        result_sink=(results if results is not None else []).append,
    )


def test_normal_turns_commit_without_redacting_canonical_history(tmp_path: Path) -> None:
    model = FakeModelClient([ModelTurn(f"first {SECRET}"), ModelTurn("second")])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())
    store = FakeStore()
    results: list[RunResult] = []
    interactive = session(
        tmp_path,
        runner,
        store,
        inputs(" first task ", "follow-up", "/exit"),
        results=results,
    )

    assert interactive.run() == 0
    assert [result.status for result in results] == [RunStatus.FINAL_RESPONSE, RunStatus.FINAL_RESPONSE]
    assert interactive.history.persisted_messages == (
        Message(Role.USER, " first task "),
        Message(Role.ASSISTANT, f"first {SECRET}"),
        Message(Role.USER, "follow-up"),
        Message(Role.ASSISTANT, "second"),
    )
    assert all(message.role is not Role.SYSTEM for saved in store.saved for message in saved.messages)
    assert [saved.provider for saved in store.saved] == ["current-provider", "current-provider"]
    assert [saved.model for saved in store.saved] == ["current-model", "current-model"]
    assert SECRET not in store.saved[0].messages[1].content
    assert "[REDACTED]" in (store.saved[0].messages[1].content or "")
    assert interactive.history.persisted_messages[1].content == f"first {SECRET}"
    assert interactive.history.persisted_messages is not store.saved[-1].messages
    assert interactive.record.model == "current-model-saved"
    assert [call[0][-1].content for call in model.calls] == [" first task ", "follow-up"]


def test_first_successful_turn_auto_names_once_from_unchanged_task(tmp_path: Path) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE, RunStatus.FINAL_RESPONSE])
    store = FakeStore()
    interactive = session(tmp_path, runner, store, inputs("/exit"))

    first = interactive.execute("请修复 Unicode parser 的测试失败")
    first_name = interactive.record.name
    second = interactive.execute("完全不同的第二个任务")

    assert first.status is RunStatus.FINAL_RESPONSE
    assert second.status is RunStatus.FINAL_RESPONSE
    assert first_name == "修复 Unicode parser 的测试失败"
    assert interactive.record.name == first_name
    assert interactive.record.name_source is SessionNameSource.AUTO
    assert [call[1] for call in runner.calls] == [
        "请修复 Unicode parser 的测试失败",
        "完全不同的第二个任务",
    ]


def test_existing_manual_name_is_never_replaced_by_auto_naming(tmp_path: Path) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE])
    store = FakeStore()
    interactive = session(tmp_path, runner, store, inputs("/exit"))
    interactive.record = replace(
        interactive.record,
        name="Legacy manual title",
        name_source=SessionNameSource.MANUAL,
    )

    interactive.execute("first successful task")

    assert interactive.record.name == "Legacy manual title"
    assert interactive.record.name_source is SessionNameSource.MANUAL


def test_failed_or_cancelled_initial_turn_does_not_auto_name(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        [RunStatus.MODEL_ERROR, RunStatus.CANCELLED, RunStatus.FINAL_RESPONSE]
    )
    interactive = session(tmp_path, runner, FakeStore(), inputs("/exit"))

    assert interactive.execute("provider failure").status is RunStatus.MODEL_ERROR
    assert interactive.record.name is None
    assert interactive.execute("cancelled task").status is RunStatus.CANCELLED
    assert interactive.record.name is None
    assert interactive.execute("请检查 pytest").status is RunStatus.FINAL_RESPONSE
    assert interactive.record.name == "检查 pytest"
    assert interactive.record.name_source is SessionNameSource.AUTO


def test_manual_rename_during_first_turn_wins_without_losing_history(
    tmp_path: Path,
) -> None:
    class BlockingRunner:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def run_turn(
            self,
            history: ConversationHistory,
            user_message: str,
        ) -> RunResult:
            self.started.set()
            assert self.release.wait(timeout=5)
            history.append(Message(Role.USER, user_message))
            history.append(Message(Role.ASSISTANT, "done"))
            return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: NOW,
        id_generator=lambda: "012345abcdef",
    )
    runner = BlockingRunner()
    interactive = InteractiveSession(
        runner=runner,  # type: ignore[arg-type]
        history=ConversationHistory("system"),
        record=store.create_session(workspace, "provider", "model"),
        store=store,
        provider="provider",
        model="model",
        sensitive_values=(),
    )
    results: list[RunResult] = []
    worker = threading.Thread(
        target=lambda: results.append(interactive.execute("first task"))
    )

    worker.start()
    assert runner.started.wait(timeout=5)
    renamed = interactive.rename("My Session")
    runner.release.set()
    worker.join(timeout=5)

    persisted = store.load_latest(workspace)
    assert not worker.is_alive()
    assert results[0].status is RunStatus.FINAL_RESPONSE
    assert renamed.name_source is SessionNameSource.MANUAL
    assert persisted is not None
    assert persisted.name == "My Session"
    assert persisted.name_source is SessionNameSource.MANUAL
    assert tuple(message.content for message in persisted.messages) == (
        "first task",
        "done",
    )


@pytest.mark.parametrize("value", ["", "  \t "])
def test_blank_input_does_not_call_runner(tmp_path: Path, value: str) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE])

    assert session(tmp_path, runner, FakeStore(), inputs(value, "/EXIT")).run() == 0
    assert runner.calls == []


@pytest.mark.parametrize("value", [" /exit ", " /ExIt\t"])
def test_exact_exit_is_case_and_whitespace_insensitive(tmp_path: Path, value: str) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE])

    assert session(tmp_path, runner, FakeStore(), inputs(value)).run() == 0
    assert runner.calls == []


def test_non_exact_exit_is_an_ordinary_task(tmp_path: Path) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE])

    assert session(tmp_path, runner, FakeStore(), inputs("/exit now", "/exit")).run() == 0
    assert [call[1] for call in runner.calls] == ["/exit now"]


@pytest.mark.parametrize("interrupt", [EOFError(), KeyboardInterrupt()])
def test_input_eof_and_interrupt_exit_normally(tmp_path: Path, interrupt: BaseException) -> None:
    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE])

    assert session(tmp_path, runner, FakeStore(), inputs(interrupt)).run() == 0
    assert runner.calls == []


def test_run_interrupt_discards_working_history_and_exits(tmp_path: Path) -> None:
    class InterruptingRunner:
        def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
            history.append(Message(Role.USER, user_message))
            raise KeyboardInterrupt()

    interactive = session(tmp_path, InterruptingRunner(), FakeStore(), inputs("task"))

    assert interactive.run() == 0
    assert interactive.history.persisted_messages == ()
    assert interactive.record.messages == ()


@pytest.mark.parametrize(
    "status",
    [RunStatus.FINAL_RESPONSE, RunStatus.MODEL_ERROR, RunStatus.MAX_STEPS, RunStatus.STALLED],
)
def test_committed_statuses_save_working_history_and_continue(
    tmp_path: Path, status: RunStatus
) -> None:
    runner = ScriptedRunner([status])
    store = FakeStore()
    results: list[RunResult] = []
    interactive = session(tmp_path, runner, store, inputs("task", "/exit"), results=results)

    assert interactive.run() == 0
    assert [result.status for result in results] == [status]
    assert len(store.saved) == 1
    if status is RunStatus.FINAL_RESPONSE:
        assert store.saved[0].messages == (
            Message(Role.USER, "task"),
            Message(Role.ASSISTANT, "answer: task"),
        )
        assert interactive.history.persisted_messages[-1] == Message(Role.ASSISTANT, "answer: task")
    else:
        assert store.saved[0].messages == (Message(Role.USER, "task"),)
        assert interactive.history.persisted_messages == (Message(Role.USER, "task"),)


def test_committed_nonfinal_status_persists_complete_tool_call_and_result_pair(
    tmp_path: Path,
) -> None:
    call = ToolCall("call-1", "missing_tool", "{}")
    runner = AgentRunner(
        FakeModelClient([ModelTurn(tool_calls=(call,))]),
        ToolRegistry(),
        ContextManager(),
        max_steps=1,
    )
    store = FakeStore()
    results: list[RunResult] = []
    interactive = session(
        tmp_path,
        runner,
        store,
        inputs("task", "/exit"),
        results=results,
    )

    assert interactive.run() == 0
    assert [result.status for result in results] == [RunStatus.MAX_STEPS]
    assert store.saved[0].messages == (
        Message(Role.USER, "task"),
        Message(Role.ASSISTANT, tool_calls=(call,)),
        Message(
            Role.TOOL,
            '{"ok":false,"output":"","error_code":"UNKNOWN_TOOL","error_message":"unknown tool: missing_tool"}',
            tool_call_id="call-1",
        ),
    )


def test_index_partial_save_error_reports_saved_session_and_unupdated_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "session-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        root,
        clock=lambda: NOW,
        id_generator=lambda: "012345abcdef",
    )
    selected = store.create_session(workspace, "provider", "model")
    real_atomic_write = session_store._atomic_write_text

    def fail_index(path: Path, text: str) -> None:
        if path.parent.name == "workspaces":
            raise PermissionError("index replacement failed")
        real_atomic_write(path, text)

    monkeypatch.setattr(session_store, "_atomic_write_text", fail_index)
    output: list[str] = []
    interactive = InteractiveSession(
        runner=ScriptedRunner([RunStatus.FINAL_RESPONSE]),  # type: ignore[arg-type]
        history=ConversationHistory("system policy"),
        record=selected,
        store=store,
        provider="provider",
        model="model",
        sensitive_values=(SECRET,),
        input_reader=inputs("task"),
        output=output.append,
    )

    assert interactive.run() == 7
    assert output == [
        "[error] SESSION_SAVE_FAILED: "
        "session was saved but workspace index was not updated"
    ]
    assert (root / "sessions" / "012345abcdef.json").is_file()


def test_internal_error_discards_turn_and_next_turn_starts_from_prior_history(tmp_path: Path) -> None:
    runner = ScriptedRunner([RunStatus.INTERNAL_ERROR, RunStatus.FINAL_RESPONSE])
    store = FakeStore()
    results: list[RunResult] = []
    interactive = session(tmp_path, runner, store, inputs("discard me", "keep me", "/exit"), results=results)

    assert interactive.run() == 0
    assert [result.status for result in results] == [RunStatus.INTERNAL_ERROR, RunStatus.FINAL_RESPONSE]
    assert len(store.saved) == 1
    assert store.saved[0].messages == (
        Message(Role.USER, "keep me"),
        Message(Role.ASSISTANT, "answer: keep me"),
    )
    assert runner.calls[1][0].persisted_messages == (
        Message(Role.USER, "keep me"),
        Message(Role.ASSISTANT, "answer: keep me"),
    )


@pytest.mark.parametrize("code", [
    "SESSION_NOT_FOUND",
    "SESSION_CORRUPT",
    "SESSION_VERSION_UNSUPPORTED",
    "SESSION_WORKSPACE_MISMATCH",
    "SESSION_INDEX_CORRUPT",
    "SESSION_IO_ERROR",
    "SESSION_SAVE_FAILED",
])
def test_persistence_errors_leave_prior_state_and_stop_input(
    tmp_path: Path, code: str
) -> None:
    class FailingStore:
        def save(self, _record: SessionRecord) -> SessionRecord:
            raise SessionError(code, f"failed with {SECRET}")

    runner = ScriptedRunner([RunStatus.FINAL_RESPONSE, RunStatus.FINAL_RESPONSE])
    output: list[str] = []
    interactive = session(
        tmp_path,
        runner,
        FailingStore(),
        inputs("first", "second", "/exit"),
        output=output,
    )

    assert interactive.run() == 7
    assert [call[1] for call in runner.calls] == ["first"]
    assert interactive.history.persisted_messages == ()
    assert interactive.record.messages == ()
    assert output == [f"[error] {code}: failed with [REDACTED]"]

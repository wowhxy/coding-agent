from __future__ import annotations

import threading

import pytest

from coding_agent.config import resolve_config
from coding_agent.model import ModelClient
from coding_agent.protocol import ToolCall, ToolDefinition, ToolResult
from coding_agent.subagents.control import create_delegate_tasks_tool
from coding_agent.subagents.manager import SubagentManager
from coding_agent.tool_execution import ToolExecutionScheduler
from coding_agent.tools import build_default_registry
from coding_agent.tools.registry import (
    RegisteredTool,
    ToolEffect,
    ToolRegistry,
)


def _tool(
    name: str,
    handler,
    *,
    effect: ToolEffect = ToolEffect.MUTATING,
    parallel_safe: bool = False,
) -> RegisteredTool:
    return RegisteredTool(
        ToolDefinition(
            name,
            f"{name} test tool.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        lambda arguments: arguments,
        handler,
        effect=effect,
        parallel_safe=parallel_safe,
    )


def test_parallel_safe_read_tools_overlap_and_results_keep_call_order() -> None:
    """Catches serial dispatch or completion-order result collection."""

    registry = ToolRegistry()
    entered = threading.Barrier(4)
    release = threading.Event()
    completion_order: list[str] = []
    completion_lock = threading.Lock()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        entered.wait(timeout=2)
        assert release.wait(timeout=2)
        with completion_lock:
            completion_order.append(call_id)
        return ToolResult(call_id, "read_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    calls = (
        ToolCall("call-a", "read_data", "{}"),
        ToolCall("call-b", "read_data", "{}"),
        ToolCall("call-c", "read_data", "{}"),
    )

    def release_after_all_enter() -> None:
        entered.wait(timeout=2)
        release.set()

    coordinator = threading.Thread(target=release_after_all_enter)
    coordinator.start()
    outcome = ToolExecutionScheduler(registry).execute(calls)
    coordinator.join(timeout=2)

    assert not coordinator.is_alive()
    assert sorted(completion_order) == ["call-a", "call-b", "call-c"]
    assert tuple(result.tool_call_id for result in outcome.results) == (
        "call-a",
        "call-b",
        "call-c",
    )
    assert outcome.stats.tool_calls_total == 3
    assert outcome.stats.parallel_groups == 1
    assert outcome.stats.parallel_calls == 3
    assert outcome.stats.serial_calls == 0


def test_production_tools_have_explicit_conservative_execution_metadata(
    tmp_path,
) -> None:
    """Catches a production tool being parallelized without an explicit policy."""

    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="fake",
        environ={"OPENAI_API_KEY": "fake-key"},
    )
    registry = build_default_registry(config)

    assert registry.execution_metadata_for("list_files") == (
        ToolEffect.READ_ONLY,
        True,
    )
    assert registry.execution_metadata_for("search_text") == (
        ToolEffect.READ_ONLY,
        True,
    )
    assert registry.execution_metadata_for("read_file") == (
        ToolEffect.READ_ONLY,
        True,
    )
    assert registry.execution_metadata_for("write_file") == (
        ToolEffect.MUTATING,
        False,
    )
    assert registry.execution_metadata_for("replace_in_file") == (
        ToolEffect.MUTATING,
        False,
    )
    assert registry.execution_metadata_for("execute_command") == (
        ToolEffect.MUTATING,
        False,
    )

    manager = SubagentManager(tmp_path, lambda: _UnusedModelClient())
    control = create_delegate_tasks_tool(manager)
    assert (control.effect, control.parallel_safe) == (ToolEffect.CONTROL, False)


class _UnusedModelClient(ModelClient):
    def complete(self, messages, tools):  # pragma: no cover - must remain unused
        raise AssertionError("model should not be called")


def test_serial_barrier_splits_parallel_groups_without_reordering() -> None:
    """Catches moving a later read ahead of a mutating call."""

    registry = ToolRegistry()
    activity: list[str] = []
    lock = threading.Lock()
    first_reads = threading.Barrier(2)
    later_reads = threading.Barrier(2)

    def read_handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        barrier = first_reads if call_id.startswith("before") else later_reads
        barrier.wait(timeout=2)
        with lock:
            activity.append(call_id)
        return ToolResult(call_id, "read_data", True, call_id)

    def write_handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        with lock:
            assert set(activity) == {"before-a", "before-b"}
            activity.append(call_id)
        return ToolResult(call_id, "write_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                read_handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
            _tool("write_data", write_handler),
        ),
        source="builtin",
    )
    calls = (
        ToolCall("before-a", "read_data", "{}"),
        ToolCall("before-b", "read_data", "{}"),
        ToolCall("write", "write_data", "{}"),
        ToolCall("after-a", "read_data", "{}"),
        ToolCall("after-b", "read_data", "{}"),
    )

    outcome = ToolExecutionScheduler(registry).execute(calls)

    assert activity[2] == "write"
    assert set(activity[:2]) == {"before-a", "before-b"}
    assert set(activity[3:]) == {"after-a", "after-b"}
    assert tuple(result.tool_call_id for result in outcome.results) == tuple(
        call.id for call in calls
    )
    assert outcome.stats.parallel_groups == 2
    assert outcome.stats.parallel_calls == 4
    assert outcome.stats.serial_calls == 1


def test_default_plugin_tools_and_mutating_tools_remain_serial() -> None:
    """Catches default/plugin or mutating registration becoming opt-out parallel."""

    registry = ToolRegistry()
    active = 0
    maximum_active = 0
    handler_threads: list[int] = []
    lock = threading.Lock()
    scheduler_thread = threading.get_ident()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        nonlocal active, maximum_active
        with lock:
            handler_threads.append(threading.get_ident())
            active += 1
            maximum_active = max(maximum_active, active)
        with lock:
            active -= 1
        return ToolResult(call_id, "plugin_default", True, call_id)

    registry.register_many(
        (_tool("plugin_default", handler),), source="plugin:default"
    )
    outcome = ToolExecutionScheduler(registry).execute(
        (
            ToolCall("a", "plugin_default", "{}"),
            ToolCall("b", "plugin_default", "{}"),
        )
    )

    assert maximum_active == 1
    assert handler_threads == [scheduler_thread, scheduler_thread]
    assert outcome.stats.parallel_groups == 0
    assert outcome.stats.parallel_calls == 0
    assert outcome.stats.serial_calls == 2


@pytest.mark.parametrize(
    ("effect", "activity_kind"),
    [
        (ToolEffect.MUTATING, "command"),
        (ToolEffect.CONTROL, "control"),
    ],
)
def test_command_and_control_calls_execute_as_serial_barriers(
    effect: ToolEffect,
    activity_kind: str,
) -> None:
    """Catches command/control metadata being dispatched to worker threads."""

    registry = ToolRegistry()
    scheduler_thread = threading.get_ident()
    handler_threads: list[int] = []

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        handler_threads.append(threading.get_ident())
        return ToolResult(call_id, "barrier_tool", True, call_id)

    registry.register_many(
        (
            RegisteredTool(
                ToolDefinition("barrier_tool", "Barrier.", {"type": "object"}),
                lambda arguments: arguments,
                handler,
                activity_kind=activity_kind,
                effect=effect,
            ),
        ),
        source="builtin" if activity_kind == "command" else "control:test",
    )

    outcome = ToolExecutionScheduler(registry).execute(
        (
            ToolCall("a", "barrier_tool", "{}"),
            ToolCall("b", "barrier_tool", "{}"),
        )
    )

    assert handler_threads == [scheduler_thread, scheduler_thread]
    assert outcome.stats.parallel_calls == 0
    assert outcome.stats.serial_calls == 2


def test_parallel_failure_is_isolated_and_handler_error_is_normalized() -> None:
    """Catches one failed read cancelling peers or leaking a Future exception."""

    registry = ToolRegistry()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        if call_id == "expected-failure":
            return ToolResult(
                call_id,
                "read_data",
                False,
                "useful stderr",
                "FILE_NOT_FOUND",
                "file is missing",
            )
        if call_id == "unexpected-error":
            raise RuntimeError("must be normalized")
        return ToolResult(call_id, "read_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    outcome = ToolExecutionScheduler(registry).execute(
        (
            ToolCall("success", "read_data", "{}"),
            ToolCall("expected-failure", "read_data", "{}"),
            ToolCall("unexpected-error", "read_data", "{}"),
        )
    )

    assert tuple(result.ok for result in outcome.results) == (True, False, False)
    assert outcome.results[1].error_code == "FILE_NOT_FOUND"
    assert outcome.results[1].output == "useful stderr"
    assert outcome.results[2].error_code == "TOOL_INTERNAL_ERROR"


def test_cancellation_finishes_running_group_and_does_not_start_next_batch() -> None:
    """Catches cancellation launching calls beyond the active read group."""

    registry = ToolRegistry()
    both_started = threading.Barrier(3)
    release = threading.Event()
    cancelled = threading.Event()
    serial_started = threading.Event()

    def read_handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        both_started.wait(timeout=2)
        assert release.wait(timeout=2)
        return ToolResult(call_id, "read_data", True, call_id)

    def write_handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        serial_started.set()
        return ToolResult(call_id, "write_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                read_handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
            _tool("write_data", write_handler),
        ),
        source="builtin",
    )
    outcome_holder = []

    def execute() -> None:
        outcome_holder.append(
            ToolExecutionScheduler(registry).execute(
                (
                    ToolCall("read-a", "read_data", "{}"),
                    ToolCall("read-b", "read_data", "{}"),
                    ToolCall("write", "write_data", "{}"),
                ),
                cancel_check=cancelled.is_set,
            )
        )

    worker = threading.Thread(target=execute)
    worker.start()
    both_started.wait(timeout=2)
    cancelled.set()
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert outcome_holder[0].cancelled is True
    assert serial_started.is_set() is False
    assert tuple(result.tool_call_id for result in outcome_holder[0].results) == (
        "read-a",
        "read-b",
    )


def test_progress_callbacks_run_on_scheduler_thread_in_original_order() -> None:
    """Catches worker-thread event emission or completion-order UI updates."""

    registry = ToolRegistry()
    release_a = threading.Event()
    b_completed = threading.Event()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        if call_id == "a":
            assert b_completed.wait(timeout=2)
            release_a.set()
        else:
            b_completed.set()
        return ToolResult(call_id, "read_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    scheduler_thread = threading.get_ident()
    starts: list[tuple[str, int]] = []
    finishes: list[tuple[str, int]] = []

    outcome = ToolExecutionScheduler(registry).execute(
        (ToolCall("a", "read_data", "{}"), ToolCall("b", "read_data", "{}")),
        start_sink=lambda call: starts.append((call.id, threading.get_ident())),
        result_sink=lambda call, _result: finishes.append(
            (call.id, threading.get_ident())
        ),
    )

    assert release_a.is_set()
    assert starts == [("a", scheduler_thread), ("b", scheduler_thread)]
    assert finishes == [("a", scheduler_thread), ("b", scheduler_thread)]
    assert tuple(result.tool_call_id for result in outcome.results) == ("a", "b")


def test_cancellation_best_effort_cancels_queued_parallel_futures() -> None:
    """Catches queued read work starting after cancellation is observed."""

    registry = ToolRegistry()
    workers_entered = threading.Barrier(3)
    release = threading.Event()
    cancel_requested = threading.Event()
    cancel_observed = threading.Event()
    entered_ids: list[str] = []
    lock = threading.Lock()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        with lock:
            entered_ids.append(call_id)
        if call_id in {"a", "b"}:
            workers_entered.wait(timeout=2)
            assert release.wait(timeout=2)
        return ToolResult(call_id, "read_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )

    def cancel_check() -> bool:
        if cancel_requested.is_set():
            cancel_observed.set()
            return True
        return False

    outcome_holder = []
    worker = threading.Thread(
        target=lambda: outcome_holder.append(
            ToolExecutionScheduler(registry, max_parallel_tools=2).execute(
                tuple(
                    ToolCall(call_id, "read_data", "{}")
                    for call_id in ("a", "b", "c", "d", "e")
                ),
                cancel_check=cancel_check,
            )
        )
    )
    worker.start()
    workers_entered.wait(timeout=2)
    cancel_requested.set()
    assert cancel_observed.wait(timeout=2)
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert outcome_holder[0].cancelled is True
    assert set(entered_ids) == {"a", "b"}
    assert tuple(result.tool_call_id for result in outcome_holder[0].results) == (
        "a",
        "b",
    )


def test_cancellation_observed_at_final_group_boundary_stops_next_agent_step() -> None:
    """Catches a final read group swallowing a just-arrived cancel request."""

    registry = ToolRegistry()
    completed = 0
    completed_lock = threading.Lock()
    all_completed = threading.Event()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        nonlocal completed
        with completed_lock:
            completed += 1
            if completed == 2:
                all_completed.set()
        return ToolResult(call_id, "read_data", True, call_id)

    registry.register_many(
        (
            _tool(
                "read_data",
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )

    outcome = ToolExecutionScheduler(registry).execute(
        (ToolCall("a", "read_data", "{}"), ToolCall("b", "read_data", "{}")),
        cancel_check=all_completed.is_set,
    )

    assert outcome.cancelled is True
    assert tuple(result.tool_call_id for result in outcome.results) == ("a", "b")

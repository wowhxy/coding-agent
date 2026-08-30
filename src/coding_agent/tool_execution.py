"""Ordered local-tool scheduling with explicit serial barriers."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from .protocol import ToolCall, ToolResult
from .tools.registry import ToolRegistry


DEFAULT_MAX_PARALLEL_TOOLS = 4
CancelCheck = Callable[[], bool]
ToolStartSink = Callable[[ToolCall], None]
ToolResultSink = Callable[[ToolCall, ToolResult], None]


@dataclass(frozen=True, slots=True)
class ToolExecutionStats:
    """Small deterministic execution summary for tests and debugging."""

    tool_calls_total: int = 0
    parallel_groups: int = 0
    parallel_calls: int = 0
    serial_calls: int = 0


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """Ordered results and non-conversation scheduler observations."""

    results: tuple[ToolResult, ...]
    stats: ToolExecutionStats
    cancelled: bool = False


class ToolExecutionScheduler:
    """Parallelize only contiguous groups of explicitly safe read tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        max_parallel_tools: int = DEFAULT_MAX_PARALLEL_TOOLS,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if type(max_parallel_tools) is not int or max_parallel_tools <= 0:
            raise ValueError("max_parallel_tools must be a positive integer")
        self.registry = registry
        self.max_parallel_tools = max_parallel_tools

    def execute(
        self,
        calls: tuple[ToolCall, ...],
        *,
        cancel_check: CancelCheck | None = None,
        start_sink: ToolStartSink | None = None,
        result_sink: ToolResultSink | None = None,
    ) -> ToolExecutionOutcome:
        """Execute calls without crossing serial barriers and preserve order."""

        if type(calls) is not tuple or any(
            not isinstance(call, ToolCall) for call in calls
        ):
            raise TypeError("calls must be a ToolCall tuple")
        for name, callback in (
            ("cancel_check", cancel_check),
            ("start_sink", start_sink),
            ("result_sink", result_sink),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable or None")

        results: list[ToolResult] = []
        parallel_groups = 0
        parallel_calls = 0
        serial_calls = 0
        cancelled = False
        pending: list[ToolCall] = []

        def cancellation_requested() -> bool:
            return cancel_check is not None and cancel_check()

        def emit_start(call: ToolCall) -> None:
            if start_sink is not None:
                start_sink(call)

        def collect(call: ToolCall, result: ToolResult) -> None:
            results.append(result)
            if result_sink is not None:
                result_sink(call, result)

        def flush_pending() -> None:
            nonlocal parallel_groups, parallel_calls, serial_calls, cancelled
            if not pending:
                return
            group = tuple(pending)
            pending.clear()
            if cancellation_requested():
                cancelled = True
                return
            if len(group) == 1:
                call = group[0]
                emit_start(call)
                collect(call, self.registry.dispatch(call))
                serial_calls += 1
                cancelled = cancellation_requested()
                return
            parallel_groups += 1
            parallel_calls += len(group)
            for call in group:
                emit_start(call)
            executor = ThreadPoolExecutor(
                max_workers=min(self.max_parallel_tools, len(group)),
                thread_name_prefix="coding-agent-tool",
            )
            futures: tuple[Future[ToolResult], ...] = ()
            try:
                futures = tuple(
                    executor.submit(self.registry.dispatch, call) for call in group
                )
                remaining = set(futures)
                while remaining:
                    if cancellation_requested():
                        cancelled = True
                        for future in remaining:
                            future.cancel()
                        break
                    _done, remaining = wait(
                        remaining,
                        timeout=0.01,
                        return_when=FIRST_COMPLETED,
                    )
                if cancelled:
                    wait(tuple(future for future in futures if not future.cancelled()))
                for call, future in zip(group, futures, strict=True):
                    if not future.cancelled():
                        collect(call, future.result())
                if cancellation_requested():
                    cancelled = True
            finally:
                executor.shutdown(wait=True, cancel_futures=cancelled)

        for call in calls:
            if self.registry.is_parallel_safe(call.name):
                pending.append(call)
                continue
            flush_pending()
            if cancelled or cancellation_requested():
                cancelled = True
                break
            emit_start(call)
            collect(call, self.registry.dispatch(call))
            serial_calls += 1
            if cancellation_requested():
                cancelled = True
                break
        if not cancelled:
            flush_pending()

        return ToolExecutionOutcome(
            tuple(results),
            ToolExecutionStats(
                tool_calls_total=len(calls),
                parallel_groups=parallel_groups,
                parallel_calls=parallel_calls,
                serial_calls=serial_calls,
            ),
            cancelled,
        )

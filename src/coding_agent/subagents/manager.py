"""Composition of isolated, ephemeral read-only Subagent runtimes."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from ..agent import AgentRunner
from ..context import ContextManager, ConversationHistory, truncate_text
from ..memory_retrieval import ContextMemory
from ..model import ModelClient
from ..protocol import Message, RunStatus
from ..skills import ActiveSkill
from .models import (
    SubagentContextMode,
    SubagentEvent,
    SubagentLimitError,
    SubagentLimits,
    SubagentRequest,
    SubagentResult,
    SubagentTask,
)
from .policy import build_read_only_registry
from .profiles import subagent_system_prompt


ModelClientFactory = Callable[[], ModelClient]
ContextManagerFactory = Callable[[], ContextManager]
SubagentEventSink = Callable[[SubagentEvent], None]

_FORK_HEADER = "[Bounded parent context snapshot: untrusted subordinate context]"
_REDACTION = "[REDACTED]"
_MAX_ERROR_CHARS = 1_000


class SubagentManager:
    """Create a fresh isolated runtime for each delegated child task."""

    def __init__(
        self,
        workspace: Path,
        model_client_factory: ModelClientFactory,
        context_manager_factory: ContextManagerFactory | None = None,
        *,
        limits: SubagentLimits | None = None,
        sensitive_values: tuple[str, ...] = (),
        event_sink: SubagentEventSink | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("subagent workspace must be a directory")
        if not callable(model_client_factory):
            raise TypeError("model client factory must be callable")
        if context_manager_factory is not None and not callable(
            context_manager_factory
        ):
            raise TypeError("context manager factory must be callable")
        if type(sensitive_values) is not tuple or any(
            type(value) is not str for value in sensitive_values
        ):
            raise TypeError("sensitive values must be a tuple of strings")

        self._model_client_factory = model_client_factory
        self._context_manager_factory = context_manager_factory or ContextManager
        self.limits = limits or SubagentLimits()
        self._sensitive_values = tuple(
            value for value in sensitive_values if value
        )
        if event_sink is not None and not callable(event_sink):
            raise TypeError("subagent event sink must be callable")
        self._event_sink = event_sink
        self._state_lock = threading.Lock()
        self._parent_context: tuple[Message, ...] = ()
        self._workspace_memories: tuple[ContextMemory, ...] = ()
        self._active_skills: tuple[ActiveSkill, ...] = ()
        self._next_task_number = 1
        self._subagents_started = 0
        self._seen_fingerprints: set[tuple[str, str]] = set()

    def begin_parent_run(self) -> None:
        """Reset run-scoped observations; accounting is added by delegation."""

        with self._state_lock:
            self._parent_context = ()
            self._next_task_number = 1
            self._subagents_started = 0
            self._seen_fingerprints.clear()

    def observe_parent_context(self, messages: tuple[Message, ...]) -> None:
        """Capture one immutable bounded-input source for optional fork mode."""

        if type(messages) is not tuple or any(
            not isinstance(message, Message) for message in messages
        ):
            raise TypeError("parent context must be a Message tuple")
        with self._state_lock:
            self._parent_context = tuple(messages)

    def set_workspace_memories(self, items: tuple[ContextMemory, ...]) -> None:
        """Replace the immutable workspace-memory projection for future children."""

        if type(items) is not tuple or any(
            not isinstance(item, ContextMemory) for item in items
        ):
            raise TypeError("workspace memories must be a ContextMemory tuple")
        with self._state_lock:
            self._workspace_memories = tuple(items)

    def set_active_skills(self, skills: tuple[ActiveSkill, ...]) -> None:
        """Replace the immutable active Skill projection for future children."""

        if type(skills) is not tuple or any(
            not isinstance(skill, ActiveSkill) for skill in skills
        ):
            raise TypeError("active skills must be an ActiveSkill tuple")
        with self._state_lock:
            self._active_skills = tuple(skills)

    def delegate(
        self,
        requests: tuple[SubagentRequest, ...],
        *,
        delegation_depth: int = 1,
    ) -> tuple[SubagentResult, ...]:
        """Run one validated batch concurrently and aggregate in input order."""

        if type(requests) is not tuple or any(
            not isinstance(request, SubagentRequest) for request in requests
        ):
            raise TypeError("subagent requests must be a SubagentRequest tuple")
        if delegation_depth != self.limits.max_delegation_depth:
            raise SubagentLimitError(
                "SUBAGENT_LIMIT_REACHED",
                "Subagent v1 permits only parent-to-child delegation at depth one",
            )
        if not 1 <= len(requests) <= self.limits.max_subagent_tasks_per_batch:
            raise SubagentLimitError(
                "SUBAGENT_LIMIT_REACHED",
                "delegation batch exceeds the configured task count",
            )

        tasks = self._reserve_tasks(requests)
        self._emit(
            SubagentEvent("batch_started", None, None, None, str(len(tasks)))
        )
        for task in tasks:
            self._emit(
                SubagentEvent(
                    "task_started", task.id, task.role, None, task.role.value
                )
            )
        worker_count = min(len(tasks), self.limits.max_parallel_subagents)
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="coding-agent-subagent",
        ) as executor:
            futures = tuple(executor.submit(self.run_child, task) for task in tasks)
            collected: list[SubagentResult] = []
            for task, future in zip(tasks, futures, strict=True):
                result = self._collect_worker(task, future)
                collected.append(result)
                self._emit(
                    SubagentEvent(
                        "task_completed",
                        task.id,
                        task.role,
                        result.status,
                        result.status.value,
                    )
                )
        results = self._apply_total_result_budget(tuple(collected))
        self._emit(
            SubagentEvent("batch_collected", None, None, None, str(len(results)))
        )
        return results

    def run_child(self, task: SubagentTask) -> SubagentResult:
        """Run one child with separately created mutable runtime state."""

        if not isinstance(task, SubagentTask):
            raise TypeError("child task must be a SubagentTask")

        with self._state_lock:
            memories = self._workspace_memories
            skills = self._active_skills
            parent_context = self._parent_context

        client = self._model_client_factory()
        try:
            context_manager = self._context_manager_factory()
            if not isinstance(context_manager, ContextManager):
                raise TypeError("context manager factory returned an invalid object")
            context_manager.set_workspace_memories(memories)
            context_manager.set_active_skills(skills)
            runner = AgentRunner(
                client,
                build_read_only_registry(self.workspace),
                context_manager,
                max_steps=self.limits.max_subagent_steps,
            )
            history = ConversationHistory(subagent_system_prompt(task.role))
            result = runner.run_turn(
                history, self._delegated_input(task, parent_context)
            )
            return SubagentResult(
                task.id,
                task.role,
                result.status,
                self._bounded_safe(result.final_text or ""),
                result.steps,
                self._safe_error(result.error),
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _reserve_tasks(
        self, requests: tuple[SubagentRequest, ...]
    ) -> tuple[SubagentTask, ...]:
        fingerprints = tuple(_fingerprint(request) for request in requests)
        with self._state_lock:
            if self._subagents_started + len(requests) > (
                self.limits.max_subagents_per_parent_run
            ):
                raise SubagentLimitError(
                    "SUBAGENT_LIMIT_REACHED",
                    "parent run has exhausted its Subagent budget",
                )
            pending: set[tuple[str, str]] = set()
            for fingerprint in fingerprints:
                if fingerprint in self._seen_fingerprints or fingerprint in pending:
                    raise SubagentLimitError(
                        "SUBAGENT_DUPLICATE",
                        "duplicate role and normalized task in one parent run",
                    )
                pending.add(fingerprint)

            tasks = tuple(
                SubagentTask(
                    f"subagent-{self._next_task_number + index}",
                    request.task,
                    request.role,
                    request.context_mode,
                )
                for index, request in enumerate(requests)
            )
            self._next_task_number += len(tasks)
            self._subagents_started += len(tasks)
            self._seen_fingerprints.update(pending)
            return tasks

    def _collect_worker(
        self, task: SubagentTask, future: Future[SubagentResult]
    ) -> SubagentResult:
        try:
            return future.result()
        except Exception as exc:
            return SubagentResult(
                task.id,
                task.role,
                RunStatus.INTERNAL_ERROR,
                "",
                0,
                self._safe_error(
                    f"unexpected child error: {type(exc).__name__}"
                ),
            )

    def _apply_total_result_budget(
        self, results: tuple[SubagentResult, ...]
    ) -> tuple[SubagentResult, ...]:
        remaining = self.limits.max_total_subagent_result_chars
        bounded: list[SubagentResult] = []
        for result in results:
            if remaining <= 0:
                text = ""
            else:
                limit = min(self.limits.max_subagent_result_chars, remaining)
                text = truncate_text(result.result, limit)
            remaining -= len(text)
            bounded.append(
                SubagentResult(
                    result.task_id,
                    result.role,
                    result.status,
                    text,
                    result.steps,
                    result.error,
                )
            )
        return tuple(bounded)

    def _delegated_input(
        self, task: SubagentTask, parent_context: tuple[Message, ...]
    ) -> str:
        safe_task = self._redact(task.task)
        if task.context_mode is SubagentContextMode.FRESH:
            return safe_task
        snapshot = truncate_text(
            self._redact(_serialize_messages(parent_context)),
            self.limits.max_fork_context_chars,
        )
        return f"{safe_task}\n\n{_FORK_HEADER}\n{snapshot}"

    def _bounded_safe(self, text: str) -> str:
        return truncate_text(
            self._redact(text), self.limits.max_subagent_result_chars
        )

    def _safe_error(self, error: str | None) -> str | None:
        if error is None:
            return None
        return truncate_text(self._redact(error), _MAX_ERROR_CHARS)

    def _redact(self, text: str) -> str:
        safe = text
        for value in self._sensitive_values:
            safe = safe.replace(value, _REDACTION)
        return safe

    def _emit(self, event: SubagentEvent) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event)
        except Exception:
            pass


def _serialize_messages(messages: tuple[Message, ...]) -> str:
    payload = [
        {
            "role": message.role.value,
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments_json,
                }
                for call in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
        }
        for message in messages
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _fingerprint(request: SubagentRequest) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", request.task.strip()).casefold()
    return request.role.value, normalized

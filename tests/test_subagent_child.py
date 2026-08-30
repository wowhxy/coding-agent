from __future__ import annotations

import json
import threading
from collections.abc import Callable

import coding_agent.subagents.manager as manager_module
from coding_agent.context import ContextManager
from coding_agent.model import ModelTransportError
from coding_agent.protocol import ModelTurn, RunStatus, ToolCall
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, ToolEffect, ToolRegistry
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import (
    SubagentContextMode,
    SubagentLimits,
    SubagentRole,
    SubagentTask,
)
from fakes import FakeModelClient


class ClosableFakeModelClient(FakeModelClient):
    def __init__(self, script: list[ModelTurn | Exception]) -> None:
        super().__init__(script)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _client_factory(
    scripts: list[list[ModelTurn | Exception]],
) -> tuple[Callable[[], ClosableFakeModelClient], list[ClosableFakeModelClient]]:
    clients: list[ClosableFakeModelClient] = []

    def create() -> ClosableFakeModelClient:
        client = ClosableFakeModelClient(scripts[len(clients)])
        clients.append(client)
        return client

    return create, clients


def _task(task_id: str = "task-1") -> SubagentTask:
    return SubagentTask(
        task_id,
        "Inspect the parser implementation.",
        SubagentRole.EXPLORE,
        SubagentContextMode.FRESH,
    )


def test_each_child_owns_and_closes_a_distinct_runtime(tmp_path) -> None:
    create_client, clients = _client_factory(
        [[ModelTurn("first finding")], [ModelTurn("second finding")]]
    )
    contexts: list[ContextManager] = []

    def create_context() -> ContextManager:
        context = ContextManager(max_context_chars=10_000)
        contexts.append(context)
        return context

    manager = SubagentManager(tmp_path, create_client, create_context)

    first = manager.run_child(_task("task-1"))
    second = manager.run_child(_task("task-2"))

    assert first.status is second.status is RunStatus.FINAL_RESPONSE
    assert first.result == "first finding"
    assert second.result == "second finding"
    assert len(clients) == len(contexts) == 2
    assert clients[0] is not clients[1]
    assert contexts[0] is not contexts[1]
    assert all(client.closed for client in clients)
    for client in clients:
        definitions = client.calls[0][1]
        assert tuple(item.name for item in definitions) == (
            "list_files",
            "search_text",
            "read_file",
        )


def test_child_client_closes_after_model_failure_and_redacts_error(tmp_path) -> None:
    create_client, clients = _client_factory(
        [[ModelTransportError("provider rejected sk-secret-value")]]
    )
    manager = SubagentManager(
        tmp_path,
        create_client,
        sensitive_values=("sk-secret-value",),
    )

    result = manager.run_child(_task())

    assert result.status is RunStatus.MODEL_ERROR
    assert result.result == ""
    assert result.error is not None
    assert "sk-secret-value" not in result.error
    assert "[REDACTED]" in result.error
    assert clients[0].closed


def test_child_enforces_its_own_maximum_step_limit(tmp_path) -> None:
    create_client, clients = _client_factory(
        [[ModelTurn(tool_calls=(ToolCall("call-1", "list_files", "{}"),))]]
    )
    limits = SubagentLimits(max_subagent_steps=1)
    manager = SubagentManager(tmp_path, create_client, limits=limits)

    result = manager.run_child(_task())

    assert result.status is RunStatus.MAX_STEPS
    assert result.steps == 1
    assert result.error == "maximum step limit reached"
    assert clients[0].closed


def test_child_cannot_call_parent_write_or_command_tools(tmp_path) -> None:
    create_client, _clients = _client_factory(
        [
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall(
                            "call-1",
                            "write_file",
                            '{"path":"created.txt","content":"bad"}',
                        ),
                        ToolCall(
                            "call-2", "execute_command", '{"command":"echo bad"}'
                        ),
                        ToolCall(
                            "call-3", "delegate_tasks", '{"tasks":[]}'
                        ),
                    )
                ),
                ModelTurn("write tools were unavailable"),
            ]
        ]
    )
    manager = SubagentManager(tmp_path, create_client)

    result = manager.run_child(_task())

    assert result.status is RunStatus.FINAL_RESPONSE
    assert not (tmp_path / "created.txt").exists()


def test_child_runner_inherits_parallel_read_tool_execution(
    tmp_path, monkeypatch
) -> None:
    """Catches child runtimes bypassing the shared AgentRunner scheduler."""

    first_started = threading.Event()
    second_started = threading.Event()

    def handler(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        if call_id == "a":
            first_started.set()
            overlap = second_started.wait(timeout=1)
        else:
            second_started.set()
            overlap = first_started.wait(timeout=1)
        return ToolResult(call_id, "read_data", True, str(overlap))

    registry = ToolRegistry()
    registry.register_many(
        (
            RegisteredTool(
                ToolDefinition("read_data", "Read.", {"type": "object"}),
                lambda arguments: arguments,
                handler,
                effect=ToolEffect.READ_ONLY,
                parallel_safe=True,
            ),
        ),
        source="builtin",
    )
    monkeypatch.setattr(
        manager_module, "build_read_only_registry", lambda _workspace: registry
    )
    create_client, clients = _client_factory(
        [
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall("a", "read_data", "{}"),
                        ToolCall("b", "read_data", "{}"),
                    )
                ),
                ModelTurn("parallel inspection complete"),
            ]
        ]
    )

    result = SubagentManager(tmp_path, create_client).run_child(_task())

    assert result.status is RunStatus.FINAL_RESPONSE
    tool_feedback = tuple(
        message
        for message in clients[0].calls[1][0]
        if message.tool_call_id in {"a", "b"}
    )
    assert tuple(
        json.loads(message.content or "{}")["output"] for message in tool_feedback
    ) == ("True", "True")

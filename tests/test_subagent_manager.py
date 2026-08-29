from __future__ import annotations

from collections.abc import Callable

import pytest

from coding_agent.context import ContextManager
from coding_agent.model import ModelTransportError
from coding_agent.protocol import ModelTurn, RunStatus, ToolCall
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import (
    SubagentContextMode,
    SubagentLimitError,
    SubagentLimits,
    SubagentRequest,
    SubagentRole,
)
from fakes import FakeModelClient


class ClosableFakeModelClient(FakeModelClient):
    def __init__(
        self,
        script: list[ModelTurn | Exception],
        *,
        close_error: Exception | None = None,
    ) -> None:
        super().__init__(script)
        self.close_error = close_error
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _factory(
    scripts: list[list[ModelTurn | Exception]],
    *,
    close_errors: tuple[Exception | None, ...] = (),
) -> tuple[Callable[[], ClosableFakeModelClient], list[ClosableFakeModelClient]]:
    clients: list[ClosableFakeModelClient] = []

    def create() -> ClosableFakeModelClient:
        index = len(clients)
        close_error = close_errors[index] if index < len(close_errors) else None
        client = ClosableFakeModelClient(scripts[index], close_error=close_error)
        clients.append(client)
        return client

    return create, clients


def _request(
    text: str,
    *,
    role: SubagentRole = SubagentRole.EXPLORE,
) -> SubagentRequest:
    return SubagentRequest(text, role, SubagentContextMode.FRESH)


def test_delegate_preserves_input_order_and_assigns_stable_run_ids(tmp_path) -> None:
    create, _clients = _factory(
        [[ModelTurn("one")], [ModelTurn("two")], [ModelTurn("three")]]
    )
    manager = SubagentManager(tmp_path, create)

    results = manager.delegate(
        (_request("first"), _request("second"), _request("third"))
    )

    assert tuple(result.task_id for result in results) == (
        "subagent-1",
        "subagent-2",
        "subagent-3",
    )
    assert tuple(result.result for result in results) == ("one", "two", "three")


def test_mixed_child_outcomes_are_isolated_in_one_successful_batch(tmp_path) -> None:
    create, clients = _factory(
        [
            [ModelTurn("finding")],
            [ModelTransportError("offline")],
            [ModelTurn(tool_calls=(ToolCall("c", "list_files", "{}"),))],
        ]
    )
    manager = SubagentManager(
        tmp_path,
        create,
        limits=SubagentLimits(max_subagent_steps=1),
    )

    results = manager.delegate(
        (_request("final"), _request("failure"), _request("max steps"))
    )

    assert tuple(result.status for result in results) == (
        RunStatus.FINAL_RESPONSE,
        RunStatus.MODEL_ERROR,
        RunStatus.MAX_STEPS,
    )
    assert all(client.closed for client in clients)


def test_worker_exception_becomes_internal_result_without_cancelling_sibling(
    tmp_path,
) -> None:
    create, clients = _factory(
        [[ModelTurn("lost")], [ModelTurn("survived")]],
        close_errors=(RuntimeError("close failed"), None),
    )
    manager = SubagentManager(tmp_path, create)

    results = manager.delegate((_request("broken"), _request("healthy")))

    assert results[0].status is RunStatus.INTERNAL_ERROR
    assert results[0].error == "unexpected child error: RuntimeError"
    assert results[1].status is RunStatus.FINAL_RESPONSE
    assert results[1].result == "survived"
    assert all(client.closed for client in clients)


def test_batch_run_duplicate_and_depth_limits_are_stable(tmp_path) -> None:
    create, _clients = _factory([[ModelTurn("ok")] for _ in range(12)])
    manager = SubagentManager(tmp_path, create)

    with pytest.raises(SubagentLimitError) as too_many:
        manager.delegate(tuple(_request(f"task {index}") for index in range(4)))
    assert too_many.value.code == "SUBAGENT_LIMIT_REACHED"

    manager.delegate((_request("One"), _request("Two"), _request("Three")))
    manager.delegate((_request("Four"), _request("Five"), _request("Six")))
    with pytest.raises(SubagentLimitError) as exhausted:
        manager.delegate((_request("Seven"),))
    assert exhausted.value.code == "SUBAGENT_LIMIT_REACHED"

    manager.begin_parent_run()
    reset = manager.delegate((_request("Seven"),))
    assert reset[0].task_id == "subagent-1"

    manager.delegate(
        (_request("  Inspect   Parser  ", role=SubagentRole.REVIEW),)
    )
    with pytest.raises(SubagentLimitError) as duplicate:
        manager.delegate((_request("inspect parser", role=SubagentRole.REVIEW),))
    assert duplicate.value.code == "SUBAGENT_DUPLICATE"

    with pytest.raises(SubagentLimitError) as nested:
        manager.delegate((_request("nested"),), delegation_depth=2)
    assert nested.value.code == "SUBAGENT_LIMIT_REACHED"


def test_duplicate_within_one_batch_is_rejected_without_consuming_ids(tmp_path) -> None:
    create, _clients = _factory([[ModelTurn("ok")]])
    manager = SubagentManager(tmp_path, create)

    with pytest.raises(SubagentLimitError) as duplicate:
        manager.delegate((_request("same task"), _request(" SAME   TASK ")))

    assert duplicate.value.code == "SUBAGENT_DUPLICATE"
    result = manager.delegate((_request("different"),))
    assert result[0].task_id == "subagent-1"


def test_result_budgets_and_redaction_are_deterministic(tmp_path) -> None:
    secret = "secret-provider-key"
    create, _clients = _factory(
        [
            [ModelTurn("A" * 7_000 + secret)],
            [ModelTurn("B" * 7_000)],
            [ModelTurn("C" * 7_000)],
        ]
    )
    limits = SubagentLimits(
        max_subagent_result_chars=6_000,
        max_total_subagent_result_chars=16_000,
    )
    manager = SubagentManager(
        tmp_path,
        create,
        limits=limits,
        sensitive_values=(secret,),
    )

    results = manager.delegate(
        (_request("first"), _request("second"), _request("third"))
    )

    assert tuple(len(result.result) for result in results) == (6_000, 6_000, 4_000)
    assert sum(len(result.result) for result in results) == 16_000
    assert all("output truncated" in result.result for result in results)
    assert secret not in "".join(result.result for result in results)


def test_empty_batch_is_rejected(tmp_path) -> None:
    manager = SubagentManager(tmp_path, lambda: FakeModelClient([]))

    with pytest.raises(SubagentLimitError) as error:
        manager.delegate(())

    assert error.value.code == "SUBAGENT_LIMIT_REACHED"

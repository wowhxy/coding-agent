from __future__ import annotations

import threading

from coding_agent.protocol import ModelTurn
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import SubagentRequest
from fakes import FakeModelClient


class BarrierClient(FakeModelClient):
    def __init__(self, barrier: threading.Barrier, answer: str) -> None:
        super().__init__([ModelTurn(answer)])
        self._barrier = barrier

    def complete(self, messages, tool_definitions):  # type: ignore[no-untyped-def]
        self._barrier.wait(timeout=5)
        return super().complete(messages, tool_definitions)


class OrderedClient(FakeModelClient):
    def __init__(
        self,
        answer: str,
        *,
        completed_second: threading.Event | None = None,
        announce_second: threading.Event | None = None,
    ) -> None:
        super().__init__([ModelTurn(answer)])
        self._completed_second = completed_second
        self._announce_second = announce_second

    def complete(self, messages, tool_definitions):  # type: ignore[no-untyped-def]
        if self._completed_second is not None:
            assert self._completed_second.wait(timeout=5)
        if self._announce_second is not None:
            self._announce_second.set()
        return super().complete(messages, tool_definitions)


def test_two_children_reach_a_barrier_concurrently_with_distinct_clients(
    tmp_path,
) -> None:
    barrier = threading.Barrier(2)
    clients: list[BarrierClient] = []
    factory_lock = threading.Lock()

    def create() -> BarrierClient:
        with factory_lock:
            client = BarrierClient(barrier, f"answer-{len(clients)}")
            clients.append(client)
            return client

    manager = SubagentManager(tmp_path, create)

    results = manager.delegate((SubagentRequest("first"), SubagentRequest("second")))

    assert len(clients) == 2
    assert clients[0] is not clients[1]
    assert {result.result for result in results} == {"answer-0", "answer-1"}


def test_reverse_worker_completion_still_returns_input_order(tmp_path) -> None:
    second_completed = threading.Event()
    clients = [
        OrderedClient("first", completed_second=second_completed),
        OrderedClient("second", announce_second=second_completed),
    ]
    factory_lock = threading.Lock()

    def create() -> OrderedClient:
        with factory_lock:
            return clients.pop(0)

    manager = SubagentManager(tmp_path, create)

    results = manager.delegate((SubagentRequest("first"), SubagentRequest("second")))

    assert tuple(result.result for result in results) == ("first", "second")


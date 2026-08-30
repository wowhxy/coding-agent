from __future__ import annotations

import threading
from pathlib import Path

import pytest

import coding_agent.application.service as service_module
from coding_agent.application.events import ProductEventKind
from coding_agent.application.service import CodingAgentService
from coding_agent.application.state import AgentState, ConversationKind
from coding_agent.config import RuntimeConfig
from coding_agent.protocol import ModelTurn, RunStatus
from coding_agent.session_store import JsonSessionStore
from tests.fakes import FakeModelClient


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake-model",
        api_key="fake-product-secret",
        api_key_env="FAKE_API_KEY",
        thinking_mode="disabled",
        sensitive_env_names=frozenset({"FAKE_API_KEY"}),
        max_steps=8,
        max_context_chars=20_000,
        recent_turns=4,
        max_tool_output_chars=2_000,
        command_timeout=5,
    )


def _service(tmp_path: Path, client: object) -> tuple[CodingAgentService, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    service = CodingAgentService.create(
        _config(workspace),
        "deepseek",
        home,
        lambda *_args: client,  # type: ignore[return-value]
    )
    return service, workspace, home


def test_service_submits_persists_and_emits_typed_lifecycle(tmp_path: Path) -> None:
    client = FakeModelClient([ModelTurn("fixed")])
    service, workspace, home = _service(tmp_path, client)
    events = []
    unsubscribe = service.subscribe(events.append)

    result = service.submit_task("fix parser")
    snapshot = service.snapshot()
    saved = JsonSessionStore(home).load_latest(workspace)

    assert result.status is RunStatus.FINAL_RESPONSE
    assert service.get_status().agent_state is AgentState.READY
    assert snapshot.status.agent_state is AgentState.READY
    assert tuple(item.kind for item in snapshot.conversation) == (
        ConversationKind.USER,
        ConversationKind.ASSISTANT,
    )
    assert saved is not None
    assert tuple(message.content for message in saved.messages) == ("fix parser", "fixed")
    assert events[0].kind is ProductEventKind.TASK_STARTED
    assert any(event.kind is ProductEventKind.MODEL_WAITING for event in events)
    assert any(event.kind is ProductEventKind.FINAL_RESPONSE for event in events)
    unsubscribe()
    service.close()


class _StreamingClient:
    def __init__(self) -> None:
        self.closed = 0

    def complete_streaming(self, _messages, _definitions, text_sink):
        text_sink("stream ")
        text_sink("complete")
        return ModelTurn("stream complete")

    def complete(self, _messages, _definitions):
        raise AssertionError("non-streaming path should not run")

    def close(self) -> None:
        self.closed += 1


def test_service_streams_redacted_deltas_and_closes_exactly_once(tmp_path: Path) -> None:
    client = _StreamingClient()
    service, _workspace, _home = _service(tmp_path, client)
    events = []
    service.subscribe(events.append)

    result = service.submit_task("stream")
    service.close()
    service.close()

    deltas = [event.title for event in events if event.kind is ProductEventKind.TEXT_DELTA]
    assert result.streamed is True
    assert deltas == ["stream ", "complete"]
    assert client.closed == 1
    assert service.snapshot().status.agent_state is AgentState.CLOSED


class _BlockingClient:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, _messages, _definitions):
        self.started.set()
        assert self.release.wait(timeout=5)
        return ModelTurn("too late")


def test_cancellation_during_provider_wait_does_not_commit_partial_turn(tmp_path: Path) -> None:
    client = _BlockingClient()
    service, workspace, home = _service(tmp_path, client)
    results = []
    thread = threading.Thread(target=lambda: results.append(service.submit_task("cancel me")))
    thread.start()
    assert client.started.wait(timeout=5)

    assert service.cancel_task() is True
    assert service.snapshot().status.agent_state is AgentState.CANCELLING
    client.release.set()
    thread.join(timeout=5)

    assert results[0].status is RunStatus.CANCELLED
    assert service.snapshot().conversation == ()
    assert JsonSessionStore(home).load_latest(workspace) is None
    assert service.snapshot().status.agent_state is AgentState.READY
    service.close()


def test_service_rejects_overlapping_or_empty_tasks(tmp_path: Path) -> None:
    client = _BlockingClient()
    service, _workspace, _home = _service(tmp_path, client)
    thread = threading.Thread(target=lambda: service.submit_task("first"))
    thread.start()
    assert client.started.wait(timeout=5)

    with pytest.raises(ValueError, match="already running"):
        service.submit_task("second")
    with pytest.raises(ValueError, match="empty"):
        service.submit_task("   ")

    service.cancel_task()
    client.release.set()
    thread.join(timeout=5)
    service.close()


def test_subscriber_failure_does_not_fail_agent_run(tmp_path: Path) -> None:
    service, _workspace, _home = _service(tmp_path, FakeModelClient([ModelTurn("done")]))

    def broken(_event) -> None:
        raise RuntimeError("UI is gone")

    service.subscribe(broken)
    result = service.submit_task("continue")

    assert result.status is RunStatus.FINAL_RESPONSE
    service.close()


def test_pre_run_snapshot_failure_restores_ready_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service, _workspace, _home = _service(
        tmp_path, FakeModelClient([ModelTurn("still usable")])
    )
    real_snapshot = service_module.snapshot_workspace
    monkeypatch.setattr(
        service_module,
        "snapshot_workspace",
        lambda _workspace: (_ for _ in ()).throw(OSError("private path")),
    )

    with pytest.raises(OSError):
        service.submit_task("first")

    assert service.snapshot().status.agent_state is AgentState.READY
    monkeypatch.setattr(service_module, "snapshot_workspace", real_snapshot)
    assert service.submit_task("second").status is RunStatus.FINAL_RESPONSE
    service.close()

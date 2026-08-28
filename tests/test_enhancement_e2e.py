from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall, ToolDefinition, ToolResult
from coding_agent.scheduler import BackgroundRuntime, BackgroundScheduler, JobStatus
from coding_agent.session_store import JsonSessionStore
from coding_agent.summary import SummaryManager
from coding_agent.tools.registry import RegisteredTool, ToolRegistry
from fakes import FakeModelClient


def test_background_session_and_new_foreground_session_complete_independently(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222"))
    times = iter(
        datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        + timedelta(seconds=index)
        for index in range(20)
    )
    store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: next(times),
        id_generator=lambda: next(ids),
    )
    first = store.create_session(workspace, "p", "m")
    foreground_model = FakeModelClient([ModelTurn("foreground complete")])
    foreground_runner = AgentRunner(
        foreground_model, ToolRegistry(), ContextManager()
    )
    interactive = InteractiveSession(
        foreground_runner,
        ConversationHistory("system"),
        first,
        store,
        "p",
        "m",
        (),
    )
    background_clients: list[FakeModelClient] = []

    def runtime() -> BackgroundRuntime:
        model = FakeModelClient([ModelTurn("background complete")])
        background_clients.append(model)
        return BackgroundRuntime(
            AgentRunner(model, ToolRegistry(), ContextManager()), lambda: None
        )

    scheduler = BackgroundScheduler(
        store, runtime, id_generator=lambda: "aaaaaaaa", max_workers=1
    )
    commands = iter(
        ("/background background task", "/new", "foreground task", "/jobs", "/exit")
    )
    output: list[str] = []
    try:
        exit_code = InteractiveShell(
            interactive,
            store,
            lambda _prompt: next(commands),
            output.append,
            scheduler=scheduler,
        ).run()
        job = scheduler.wait("aaaaaaaa", timeout=2)
    finally:
        scheduler.shutdown()

    assert exit_code == 0
    assert job.status is JobStatus.COMPLETED
    first_saved = store.load_session("111111111111", workspace)
    second_saved = store.load_session("222222222222", workspace)
    assert [message.content for message in first_saved.messages if message.role is Role.USER] == [
        "background task"
    ]
    assert [message.content for message in second_saved.messages if message.role is Role.USER] == [
        "foreground task"
    ]
    assert background_clients and foreground_model.calls
    assert any("aaaaaaaa" in line for line in output)


def test_streaming_tool_loop_receives_summary_and_workspace_memory() -> None:
    history = ConversationHistory("system", "original goal")
    history.append(Message(Role.ASSISTANT, "old answer " + "x" * 200))
    history.append(Message(Role.USER, "previous question"))
    history.append(Message(Role.ASSISTANT, "previous answer"))
    context = ContextManager(max_context_chars=4_000, recent_turns=1)
    context.set_workspace_memory("tests use pytest")
    summary_model = FakeModelClient([ModelTurn("old work summarized")])

    class StreamingModel:
        def __init__(self) -> None:
            self.turns = iter(
                (
                    ModelTurn(tool_calls=(ToolCall("call-1", "inspect", "{}"),)),
                    ModelTurn("verified final"),
                )
            )
            self.calls: list[tuple[object, object]] = []

        def complete(self, messages, tools):
            raise AssertionError("streaming path expected")

        def complete_streaming(self, messages, tools, sink):
            self.calls.append((tuple(messages), tuple(tools)))
            turn = next(self.turns)
            if turn.final_text:
                sink(turn.final_text)
            return turn

    model = StreamingModel()
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            ToolDefinition("inspect", "inspect", {"type": "object"}),
            lambda arguments: arguments,
            lambda call_id, _arguments: ToolResult(call_id, "inspect", True, "ok"),
        )
    )
    streamed: list[str] = []
    runner = AgentRunner(
        model,  # type: ignore[arg-type]
        registry,
        context,
        text_sink=streamed.append,
        summary_manager=SummaryManager(
            summary_model, threshold_chars=1, recent_turns=1
        ),
    )

    result = runner.run_turn(history, "current task")

    first_request = model.calls[0][0]
    assert result.final_text == "verified final" and result.streamed is True
    assert streamed == ["verified final"]
    assert any("Workspace memory" in (message.content or "") for message in first_request)
    assert any("old work summarized" in (message.content or "") for message in first_request)
    assert any(message.role is Role.TOOL for message in history.messages)

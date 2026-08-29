from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.memory_candidate import MemoryCandidate, MemoryCandidateExtractor
from coding_agent.protocol import Message, ModelTurn, Role, RunResult, RunStatus, ToolCall
from coding_agent.session_store import JsonSessionStore
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)


def test_extractor_only_calls_model_for_tool_evidence_or_long_term_constraint() -> None:
    model = FakeModelClient(
        [
            ModelTurn(
                '{"candidates":[{"key":"test.command",'
                '"content":"Run pytest before finishing",'
                '"kind":"command","source":"observed"}]}'
            )
        ]
    )
    extractor = MemoryCandidateExtractor(model)

    assert extractor.extract((Message(Role.USER, "fix this once"),)) == ()
    candidates = extractor.extract(
        (
            Message(Role.USER, "inspect the project"),
            Message(
                Role.ASSISTANT,
                tool_calls=(ToolCall("call-1", "read_file", '{"path":"README"}'),),
            ),
            Message(Role.TOOL, "project docs", tool_call_id="call-1"),
            Message(Role.ASSISTANT, "done"),
        )
    )

    assert candidates == (
        MemoryCandidate(
            "test.command", "Run pytest before finishing", "command", "observed"
        ),
    )
    assert len(model.calls) == 1
    assert model.calls[0][1] == ()


def test_extractor_is_bounded_and_failures_are_silent() -> None:
    payload = {
        "candidates": [
            {
                "key": f"convention.item-{index}",
                "content": f"Stable convention {index}",
                "kind": "convention",
                "source": "user",
            }
            for index in range(6)
        ]
    }
    import json

    model = FakeModelClient(
        [
            ModelTurn(json.dumps(payload)),
            ModelTurn("not json"),
            ModelTurn(tool_calls=(ToolCall("x", "read_file", "{}"),)),
            RuntimeError("offline failure"),
        ]
    )
    extractor = MemoryCandidateExtractor(model)
    eligible = (Message(Role.USER, "from now on always run tests"),)

    assert len(extractor.extract(eligible)) == 4
    assert extractor.extract(eligible) == ()
    assert extractor.extract(eligible) == ()
    assert extractor.extract(eligible) == ()


def test_extractor_rejects_transient_or_unsafe_candidate_shapes() -> None:
    model = FakeModelClient(
        [
            ModelTurn(
                '{"candidates":['
                '{"key":"debug.note","content":"temporary debug output from today",'
                '"kind":"fact","source":"observed"},'
                '{"key":"source.dump","content":"x = 1\\nprint(x)",'
                '"kind":"fact","source":"observed"},'
                '{"key":"module.size","content":"Keep modules small",'
                '"kind":"unknown","source":"user"},'
                '{"key":"python.version","content":"Use Python 3.11",'
                '"kind":"architecture","source":"user"}'
                "]}"
            )
        ]
    )

    assert MemoryCandidateExtractor(model).extract(
        (Message(Role.USER, "the project must use Python 3.11"),)
    ) == (
        MemoryCandidate(
            "python.version", "Use Python 3.11", "architecture", "user"
        ),
    )


class _FinalRunner:
    def __init__(self) -> None:
        self.memory = ""

    def run_turn(self, history: ConversationHistory, text: str) -> RunResult:
        history.append(Message(Role.USER, text))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)

    def set_workspace_memory(self, text: str) -> None:
        self.memory = text


class _Extractor:
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []

    def extract(self, messages: tuple[Message, ...]) -> tuple[MemoryCandidate, ...]:
        self.calls.append(messages)
        content = "Use pytest" if len(self.calls) == 1 else "Use ruff"
        key = "test.command" if len(self.calls) == 1 else "lint.command"
        return (MemoryCandidate(key, content, "command", "observed"),)


def test_shell_requires_confirmation_and_defaults_to_reject(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: NOW,
        id_generator=lambda: "aaaaaaaaaaaa",
    )
    memory_store = WorkspaceMemoryStore(
        tmp_path / "home",
        clock=lambda: NOW,
        id_generator=iter(("11111111", "22222222")).__next__,
    )
    runner = _FinalRunner()
    session = InteractiveSession(
        runner,  # type: ignore[arg-type]
        ConversationHistory("system"),
        session_store.create_session(workspace, "p", "m"),
        session_store,
        "p",
        "m",
        (),
    )
    extractor = _Extractor()
    answers = iter(("first task", "y", "second task", "", "/exit"))
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    assert InteractiveShell(
        session,
        session_store,
        read,
        lambda _message: None,
        memory_store,
        candidate_extractor=extractor,  # type: ignore[arg-type]
    ).run() == 0

    assert [item.text for item in memory_store.list(workspace)] == ["Use pytest"]
    assert len(extractor.calls) == 2
    assert all(call[0].role is Role.USER for call in extractor.calls)
    assert sum("[y/N]" in prompt for prompt in prompts) == 2
    assert "Use pytest" in runner.memory


def test_shell_conflict_requires_separate_replace_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_store = JsonSessionStore(
        tmp_path / "sessions",
        clock=lambda: NOW,
        id_generator=lambda: "aaaaaaaaaaaa",
    )
    memory_store = WorkspaceMemoryStore(
        tmp_path / "sessions",
        clock=lambda: NOW,
        id_generator=lambda: "11111111",
    )
    original = memory_store.add(
        workspace, "Test runner: pytest", (), kind="command"
    )
    runner = _FinalRunner()
    session = InteractiveSession(
        runner,  # type: ignore[arg-type]
        ConversationHistory("system"),
        session_store.create_session(workspace, "p", "m"),
        session_store,
        "p",
        "m",
        (),
    )

    class ConflictExtractor:
        def extract(self, _messages: tuple[Message, ...]):
            return (
                MemoryCandidate("test.runner", "unittest", "command", "observed"),
            )

    prompts: list[str] = []
    answers = iter(("task one", "n", "task two", "y", "/exit"))

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    assert InteractiveShell(
        session,
        session_store,
        read,
        lambda _message: None,
        memory_store,
        candidate_extractor=ConflictExtractor(),  # type: ignore[arg-type]
    ).run() == 0

    replaced = memory_store.list(workspace)[0]
    assert replaced.id == original.id
    assert replaced.key == "test.runner"
    assert replaced.content == "unittest"
    assert replaced.source == "confirmed_candidate"
    assert sum("replace" in prompt and "[y/N]" in prompt for prompt in prompts) == 2

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.cli import main
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.model import ModelProtocolError, ModelTransportError
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall
from coding_agent.session_store import JsonSessionStore
from coding_agent.skill_selector import SkillActivator, SkillSelector
from coding_agent.skills import SkillMetadata, SkillRegistry
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def _metadata(name: str, description: str = "Useful workflow.") -> SkillMetadata:
    return SkillMetadata(name, description, "user", Path(name) / "SKILL.md")


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Useful workflow.",
    body: str = "Inspect, test, edit, and verify.",
) -> Path:
    package = root / name
    package.mkdir(parents=True)
    path = package / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_selector_sends_metadata_only_with_no_tools_and_filters_names() -> None:
    model = FakeModelClient(
        [ModelTurn('{"skills":["beta","missing","beta","alpha"]}')]
    )
    selector = SkillSelector(model)
    metadata = (
        _metadata("alpha", "Alpha metadata only."),
        _metadata("beta", "Beta metadata only."),
    )

    result = selector.select("repair the build", metadata, limit=2)

    assert result.names == ("beta", "alpha")
    assert result.diagnostic is None
    request, tools = model.calls[0]
    rendered = "\n".join(message.content or "" for message in request)
    assert tools == ()
    assert "repair the build" in rendered
    assert "Alpha metadata only." in rendered
    assert "Beta metadata only." in rendered
    assert "Inspect, test, edit" not in rendered


@pytest.mark.parametrize(
    "response",
    (
        ModelTurn(None),
        ModelTurn("not-json"),
        ModelTurn("[]"),
        ModelTurn('{"skills":"alpha"}'),
        ModelTurn('{"skills":[1]}'),
        ModelTurn("{}", (ToolCall("1", "unexpected", "{}"),)),
        ModelTransportError("private provider failure"),
        ModelProtocolError("private protocol failure"),
    ),
)
def test_selector_failures_are_generic_best_effort(response: object) -> None:
    model = FakeModelClient([response])  # type: ignore[list-item]

    result = SkillSelector(model).select("task", (_metadata("alpha"),))

    assert result.names == ()
    assert result.diagnostic is not None
    assert result.diagnostic.code == "SKILL_SELECTOR_FAILED"
    assert "private" not in result.diagnostic.message


def test_selector_skips_model_call_when_no_metadata_or_capacity() -> None:
    model = FakeModelClient([])
    selector = SkillSelector(model)

    assert selector.select("task", ()).names == ()
    assert selector.select("task", (_metadata("alpha"),), limit=0).names == ()
    assert model.calls == []


def test_activator_keeps_manual_priority_and_recovers_from_changed_auto_skill(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    _write_skill(home / "skills", "manual")
    changed = _write_skill(home / "skills", "automatic")
    registry = SkillRegistry(home, workspace)
    registry.discover()
    model = FakeModelClient([ModelTurn('{"skills":["automatic"]}')])
    activator = SkillActivator(registry, SkillSelector(model))
    changed.unlink()

    result = activator.prepare("task", ("manual",))

    assert [(item.skill.metadata.name, item.activation) for item in result.skills] == [
        ("manual", "manual")
    ]
    assert [item.code for item in result.diagnostics] == [
        "SKILL_ACTIVATION_FAILED"
    ]


def test_one_shot_selection_is_pre_turn_and_not_conversation_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(
        workspace / ".coding-agent" / "skills",
        "cpp-cmake",
        body="Read CMakeLists.txt before changing the build.",
    )
    client = FakeModelClient(
        [ModelTurn('{"skills":["cpp-cmake"]}'), ModelTurn("done")]
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--base-url",
            "https://example.test/v1",
            "--model",
            "test-model",
            "repair cmake",
        ],
        environ={"OPENAI_API_KEY": "fake", "CODING_AGENT_HOME": str(home)},
        client_factory=lambda *_args: client,
    )

    assert exit_code == 0
    assert len(client.calls) == 2
    assert client.calls[0][1] == ()
    agent_request = client.calls[1][0]
    assert agent_request[0].role is Role.SYSTEM
    assert "Subordinate Skill Guidance" in (agent_request[1].content or "")
    assert agent_request[2] == Message(Role.USER, "repair cmake")
    assert all(
        '{"skills"' not in (message.content or "") for message in agent_request
    )
    assert capsys.readouterr().err == ""


def test_foreground_and_multiline_each_select_once_without_persisting_selector(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(home / "skills", "method")
    registry = SkillRegistry(home, workspace)
    registry.discover()
    client = FakeModelClient(
        [
            ModelTurn('{"skills":["method"]}'),
            ModelTurn("first done"),
            ModelTurn('{"skills":["method"]}'),
            ModelTurn("second done"),
        ]
    )
    runner = AgentRunner(client, ToolRegistry(), ContextManager())
    store = JsonSessionStore(
        tmp_path / "sessions", clock=lambda: NOW, id_generator=lambda: "111111111111"
    )
    session = InteractiveSession(
        runner,
        ConversationHistory("core"),
        store.create_session(workspace, "p", "m"),
        store,
        "p",
        "m",
        (),
    )
    commands = iter(("first task", "/multiline", "second", "task", "/send", "/exit"))
    shell = InteractiveShell(
        session,
        store,
        lambda _prompt: next(commands),
        lambda _line: None,
        skill_registry=registry,
        skill_activator=SkillActivator(registry, SkillSelector(client)),
    )

    assert shell.run() == 0

    assert len(client.calls) == 4
    assert client.calls[0][1] == client.calls[2][1] == ()
    assert all(
        "Subordinate Skill Guidance" in (client.calls[index][0][1].content or "")
        for index in (1, 3)
    )
    persisted = session.history.persisted_messages
    assert [item.content for item in persisted if item.role is Role.USER] == [
        "first task",
        "second\ntask",
    ]
    assert all('{"skills"' not in (item.content or "") for item in persisted)

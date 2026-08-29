from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.context import ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.interactive_shell import InteractiveShell
from coding_agent.protocol import Message, Role, RunResult, RunStatus
from coding_agent.session_store import JsonSessionStore
from coding_agent.skills import ManualSkillState, SkillError, SkillRegistry


NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)


def _write_skill(root: Path, name: str, body: str = "Follow this method.") -> None:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Guidance for {name}.\n---\n\n{body}\n",
        encoding="utf-8",
    )


class _Runner:
    def run_turn(self, history: ConversationHistory, user_message: str) -> RunResult:
        history.append(Message(Role.USER, user_message))
        history.append(Message(Role.ASSISTANT, "done"))
        return RunResult(RunStatus.FINAL_RESPONSE, "done", 1, None)


def _shell(
    tmp_path: Path,
    commands: tuple[str, ...],
    *,
    ids: tuple[str, ...] = ("111111111111", "222222222222"),
) -> tuple[InteractiveShell, InteractiveSession, SkillRegistry, list[str]]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    id_values = iter(ids)
    store = JsonSessionStore(
        tmp_path / "session-home",
        clock=lambda: NOW,
        id_generator=lambda: next(id_values),
    )
    initial = store.save(store.create_session(workspace, "provider", "model"))
    session = InteractiveSession(
        _Runner(),  # type: ignore[arg-type]
        ConversationHistory("system"),
        initial,
        store,
        "provider",
        "model",
        (),
    )
    registry = SkillRegistry(home, workspace)
    output: list[str] = []
    command_values = iter(commands)
    shell = InteractiveShell(
        session,
        store,
        lambda _prompt: next(command_values),
        output.append,
        skill_registry=registry,
    )
    return shell, session, registry, output


def test_manual_state_is_ordered_transactional_and_bounded(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    for name in ("alpha", "bravo", "charlie", "delta"):
        _write_skill(home / "skills", name)
    registry = SkillRegistry(home, workspace)
    registry.discover()
    state = ManualSkillState()

    state.use("session-a", "alpha", registry)
    state.use("session-a", "bravo", registry)
    state.use("session-a", "alpha", registry)
    state.use("session-a", "charlie", registry)

    assert state.names("session-a") == ("alpha", "bravo", "charlie")
    assert state.names("session-b") == ()
    with pytest.raises(SkillError) as raised:
        state.use("session-a", "delta", registry)
    assert raised.value.error_code == "SKILL_LIMIT"
    assert state.names("session-a") == ("alpha", "bravo", "charlie")


def test_manual_state_rejects_aggregate_body_limit_without_partial_change(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    _write_skill(home / "skills", "first", "a" * 10_000)
    _write_skill(home / "skills", "second", "b" * 10_000)
    _write_skill(home / "skills", "third", "c")
    registry = SkillRegistry(home, workspace)
    registry.discover()
    state = ManualSkillState()
    state.use("session", "first", registry)
    state.use("session", "second", registry)

    with pytest.raises(SkillError) as raised:
        state.use("session", "third", registry)

    assert raised.value.error_code == "SKILL_ACTIVE_BODY_LIMIT"
    assert state.names("session") == ("first", "second")


def test_shell_lists_and_changes_manual_skills(tmp_path: Path) -> None:
    shell, _session, registry, output = _shell(
        tmp_path,
        ("/skills", "/skill use cpp-cmake", "/skills", "/skill off cpp-cmake", "/skill clear", "/exit"),
    )
    _write_skill(tmp_path / "home" / "skills", "cpp-cmake")
    registry.discover()

    assert shell.run() == 0

    rows = [line for line in output if line.startswith("[skill] cpp-cmake")]
    assert rows == [
        "[skill] cpp-cmake  user  inactive",
        "[skill] cpp-cmake  user  active",
    ]
    assert "[skill] active: cpp-cmake" in output
    assert "[skill] inactive: cpp-cmake" in output
    assert "[skill] cleared" in output


def test_manual_pins_follow_session_id_in_process_and_delete_removes_state(
    tmp_path: Path,
) -> None:
    shell, session, registry, output = _shell(
        tmp_path,
        (
            "/skill use cpp-cmake",
            "/new",
            "/skills",
            "/use 111111111111",
            "/skills",
            "/delete",
            "/exit",
        ),
        ids=("111111111111", "222222222222", "333333333333"),
    )
    _write_skill(tmp_path / "home" / "skills", "cpp-cmake")
    registry.discover()

    assert shell.run() == 0

    status_rows = [line for line in output if line.startswith("[skill] cpp-cmake")]
    assert status_rows == [
        "[skill] cpp-cmake  user  inactive",
        "[skill] cpp-cmake  user  active",
    ]
    assert shell.manual_skills.names("111111111111") == ()
    assert session.record.session_id == "333333333333"


def test_new_shell_does_not_restore_manual_pins(tmp_path: Path) -> None:
    shell, session, registry, _output = _shell(
        tmp_path, ("/skill use cpp-cmake", "/exit")
    )
    _write_skill(tmp_path / "home" / "skills", "cpp-cmake")
    registry.discover()
    shell.run()

    restarted = InteractiveShell(
        session,
        shell.store,
        lambda _prompt: "/exit",
        lambda _line: None,
        skill_registry=registry,
    )

    assert restarted.manual_skills.names(session.record.session_id) == ()


def test_skill_command_errors_are_safe_and_do_not_exit_shell(tmp_path: Path) -> None:
    shell, _session, _registry, output = _shell(
        tmp_path, ("/skill use private-input", "/skill off private-input", "/exit")
    )

    assert shell.run() == 0

    errors = [line for line in output if line.startswith("[error]")]
    assert [line.split(":", 1)[0] for line in errors] == [
        "[error] SKILL_NOT_FOUND",
        "[error] SKILL_NOT_ACTIVE",
    ]
    assert all("private-input" not in line for line in errors)

from __future__ import annotations

import json
import shutil
from pathlib import Path

from coding_agent.application.service import CodingAgentService
from coding_agent.application.state import AgentState
from coding_agent.config import RuntimeConfig
from coding_agent.protocol import ModelTurn, ToolCall
from coding_agent.session_store import JsonSessionStore
from tests.fakes import FakeModelClient


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=workspace.resolve(), base_url="https://example.test/v1",
        model="fake", api_key="secret", api_key_env="FAKE_KEY",
        thinking_mode="disabled", sensitive_env_names=frozenset({"FAKE_KEY"}),
        max_steps=8, max_context_chars=20_000, recent_turns=4,
        max_tool_output_chars=2_000, command_timeout=5,
    )


def _write_skill(home: Path) -> None:
    package = home / "skills" / "tdd"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: Test first.\n---\n\nWrite a failing test first.\n",
        encoding="utf-8",
    )


def _create(
    tmp_path: Path,
    script: list[ModelTurn],
    *,
    with_skill: bool = False,
) -> tuple[CodingAgentService, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    if with_skill:
        _write_skill(home)
    client = FakeModelClient(script)
    return (
        CodingAgentService.create(_config(workspace), "custom", home, lambda *_args: client),
        workspace,
        home,
    )


def test_session_navigation_rename_delete_and_resume_are_transactional(tmp_path: Path) -> None:
    service, workspace, home = _create(
        tmp_path,
        [ModelTurn("first done"), ModelTurn("second done")],
    )
    first_id = service.snapshot().status.session_id
    service.submit_task("first")
    service.rename_session("Parser fix")
    second = service.new_session()
    service.submit_task("second")

    sessions = service.list_sessions()
    assert {item.display_name for item in sessions} == {"Parser fix", f"Session {second.session_id[:6]}"}
    service.switch_session(first_id)
    assert service.snapshot().conversation[-1].content == "first done"
    service.delete_session()
    assert service.snapshot().status.session_id == second.session_id
    service.close()

    resumed = CodingAgentService.create(
        _config(workspace), "custom", home,
        lambda *_args: FakeModelClient([ModelTurn("resumed")]),
    )
    assert resumed.snapshot().status.session_id == second.session_id
    assert resumed.snapshot().conversation[-1].content == "second done"
    resumed.close()


def test_memory_and_skill_management_refresh_product_status(tmp_path: Path) -> None:
    service, _workspace, _home = _create(tmp_path, [], with_skill=True)

    memory = service.add_memory("test.command = pytest -q")
    assert service.list_memory()[0].key == "test.command"
    assert service.snapshot().status.memory_count == 1
    service.use_skill("tdd")
    assert service.snapshot().status.active_skills == ("tdd",)
    assert service.list_skills()[0].activation == "manual"
    service.off_skill("tdd")
    assert service.snapshot().status.active_skills == ()
    assert service.delete_memory(memory.id) is True
    assert service.list_memory() == ()
    service.close()


def test_plugin_management_preserves_trust_warning(tmp_path: Path) -> None:
    service, _workspace, home = _create(tmp_path, [])
    service.close()
    shutil.copytree(Path("examples/plugins/git-readonly"), home / "plugins" / "git-readonly")
    service = CodingAgentService.create(
        _config(tmp_path / "workspace"), "custom", home,
        lambda *_args: FakeModelClient([]),
    )

    enabled = service.enable_plugin("git-readonly")
    assert enabled.enabled is True
    assert "trusted local code" in enabled.trust_warning
    assert service.snapshot().status.enabled_plugins == ("git-readonly",)
    service.disable_plugin("git-readonly")
    assert service.snapshot().status.enabled_plugins == ()
    service.close()


def test_memory_candidate_requires_explicit_confirmation(tmp_path: Path) -> None:
    candidate_json = json.dumps(
        {"candidates": [{"key": "test.command", "content": "pytest -q", "kind": "command", "source": "observed"}]}
    )
    service, _workspace, _home = _create(
        tmp_path,
        [
            ModelTurn(tool_calls=(ToolCall("c1", "execute_command", '{"command":"python -c \\"print(1)\\""}'),)),
            ModelTurn("done"),
            ModelTurn(candidate_json),
        ],
    )

    service.submit_task("inspect")
    candidates = service.pending_candidates()
    assert len(candidates) == 1
    assert service.list_memory() == ()
    saved = service.confirm_candidate(candidates[0].id, accept=True)
    assert saved is not None
    assert service.list_memory()[0].key == "test.command"
    assert service.pending_candidates() == ()
    service.close()


def test_recall_is_same_workspace_and_temporary(tmp_path: Path) -> None:
    service, workspace, home = _create(
        tmp_path,
        [ModelTurn("test_parser_unicode failed")],
    )
    service.submit_task("investigate Unicode parser")
    old_id = service.snapshot().status.session_id
    service.new_session()

    results = service.recall("Unicode parser failed")

    assert results
    assert all(item.session_id == old_id for item in results)
    assert service.list_memory() == ()
    assert service.snapshot().status.agent_state is AgentState.READY
    service.close()

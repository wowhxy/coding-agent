from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.protocol import ToolCall
from coding_agent.subagents.models import SubagentRole
from coding_agent.subagents.policy import build_read_only_registry
from coding_agent.subagents.profiles import subagent_system_prompt


def test_read_only_registry_exposes_exactly_three_existing_file_tools(
    tmp_path: Path,
) -> None:
    (tmp_path / "parser.py").write_text("VALUE = 1\n", encoding="utf-8")
    registry = build_read_only_registry(tmp_path)

    assert tuple(item.name for item in registry.definitions()) == (
        "list_files",
        "search_text",
        "read_file",
    )
    result = registry.dispatch(
        ToolCall("read", "read_file", '{"path":"parser.py"}')
    )
    assert result.ok is True
    assert "VALUE = 1" in result.output


@pytest.mark.parametrize(
    "name",
    (
        "write_file",
        "replace_in_file",
        "execute_command",
        "delegate_tasks",
        "git_status",
    ),
)
def test_read_only_registry_has_no_mutating_control_or_plugin_capability(
    tmp_path: Path, name: str
) -> None:
    result = build_read_only_registry(tmp_path).dispatch(
        ToolCall("denied", name, "{}")
    )

    assert result.error_code == "UNKNOWN_TOOL"


def test_read_only_registry_reuses_workspace_containment(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")

    result = build_read_only_registry(workspace).dispatch(
        ToolCall(
            "escape",
            "read_file",
            json.dumps({"path": "../outside.txt"}),
        )
    )

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert "private" not in result.output


def test_role_profiles_keep_read_only_core_rules_and_distinct_guidance() -> None:
    prompts = {
        role: subagent_system_prompt(role)
        for role in SubagentRole
    }

    assert len(set(prompts.values())) == 3
    for prompt in prompts.values():
        assert "read-only" in prompt
        assert "Do not modify" in prompt
        assert "delegate" in prompt
        assert "Skill" in prompt

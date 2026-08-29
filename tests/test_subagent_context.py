from __future__ import annotations

from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.memory_retrieval import ContextMemory
from coding_agent.protocol import Message, ModelTurn, Role
from coding_agent.skills import ActiveSkill, Skill, SkillMetadata
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import (
    SubagentContextMode,
    SubagentLimits,
    SubagentRole,
    SubagentTask,
)
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


def _active_skill(name: str, body: str) -> ActiveSkill:
    metadata = SkillMetadata(name, "test guidance", "user", Path(name) / "SKILL.md")
    return ActiveSkill(Skill(metadata, body), "manual")


def _combined_contents(client: FakeModelClient) -> str:
    return "\n".join(
        message.content or "" for message in client.calls[0][0]
    )


def test_fresh_child_receives_task_memory_and_skills_but_not_parent_context(
    tmp_path,
) -> None:
    client = FakeModelClient([ModelTurn("finding")])
    manager = SubagentManager(tmp_path, lambda: client)
    manager.set_workspace_memories(
        (ContextMemory("mem-1", "architecture", "parser.path", "src/parser.py"),)
    )
    manager.set_active_skills((_active_skill("review-code", "Check boundary cases."),))
    parent = (
        Message(Role.SYSTEM, "parent core"),
        Message(Role.USER, "PARENT_PRIVATE_CONTEXT"),
    )
    manager.observe_parent_context(parent)
    task = SubagentTask(
        "fresh-1",
        "Inspect the parser path.",
        SubagentRole.ANALYSIS,
        SubagentContextMode.FRESH,
    )

    manager.run_child(task)

    contents = _combined_contents(client)
    assert "Inspect the parser path." in contents
    assert "src/parser.py" in contents
    assert "Check boundary cases." in contents
    assert "PARENT_PRIVATE_CONTEXT" not in contents
    assert parent == (
        Message(Role.SYSTEM, "parent core"),
        Message(Role.USER, "PARENT_PRIVATE_CONTEXT"),
    )


def test_fork_child_receives_bounded_immutable_parent_context_snapshot(tmp_path) -> None:
    client = FakeModelClient([ModelTurn("finding")])
    limits = SubagentLimits(max_fork_context_chars=300)
    manager = SubagentManager(tmp_path, lambda: client, limits=limits)
    parent = (
        Message(Role.SYSTEM, "parent core"),
        Message(Role.USER, "EARLY_PARENT_FACT" + "x" * 1_000),
        Message(Role.ASSISTANT, "LATEST_PARENT_FACT"),
    )
    manager.observe_parent_context(parent)
    task = SubagentTask(
        "fork-1",
        "Review the current approach.",
        SubagentRole.REVIEW,
        SubagentContextMode.FORK,
    )

    manager.run_child(task)

    contents = _combined_contents(client)
    assert "Bounded parent context snapshot" in contents
    assert "LATEST_PARENT_FACT" in contents
    assert "output truncated" in contents
    delegated_user = next(
        message.content or ""
        for message in client.calls[0][0]
        if message.role is Role.USER
    )
    assert len(delegated_user) <= len(task.task) + limits.max_fork_context_chars + 100
    assert parent[1].content == "EARLY_PARENT_FACT" + "x" * 1_000


def test_agent_runner_exposes_generic_run_start_and_context_snapshot_hooks() -> None:
    model = FakeModelClient([ModelTurn("done")])
    starts: list[str] = []
    snapshots: list[tuple[Message, ...]] = []
    runner = AgentRunner(
        model,
        ToolRegistry(),
        ContextManager(),
        run_start_hook=lambda: starts.append("started"),
        context_snapshot_sink=snapshots.append,
    )
    history = ConversationHistory("system")

    runner.run_turn(history, "task")

    assert starts == ["started"]
    assert len(snapshots) == 1
    assert snapshots[0] == model.calls[0][0]
    assert snapshots[0] is not history.messages


def test_delegated_task_and_fork_snapshot_redact_configured_sensitive_values(
    tmp_path,
) -> None:
    secret = "provider-secret-value"
    client = FakeModelClient([ModelTurn("finding")])
    manager = SubagentManager(
        tmp_path,
        lambda: client,
        sensitive_values=(secret,),
    )
    manager.observe_parent_context((Message(Role.USER, f"parent {secret}"),))
    task = SubagentTask(
        "fork-secret",
        f"Inspect without exposing {secret}",
        SubagentRole.REVIEW,
        SubagentContextMode.FORK,
    )

    manager.run_child(task)

    contents = _combined_contents(client)
    assert secret not in contents
    assert contents.count("[REDACTED]") >= 2

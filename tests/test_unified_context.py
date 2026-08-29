from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.context_policy import ContextPolicy
from coding_agent.memory_retrieval import ContextMemory
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall, ToolResult
from coding_agent.recall import RecallEntry
from coding_agent.skills import ActiveSkill, Skill, SkillMetadata
from coding_agent.summary import SummaryManager, SummaryState
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


def _skill(name: str, body: str) -> ActiveSkill:
    return ActiveSkill(
        Skill(
            SkillMetadata(name, "method guidance", "user", Path(name) / "SKILL.md"),
            body,
        ),  # type: ignore[arg-type]
        "manual",
    )


def test_final_context_order_and_safe_report_cover_all_layers() -> None:
    history = ConversationHistory("core", "original task")
    history.append(Message(Role.ASSISTANT, "old answer"))
    history.append(Message(Role.USER, "latest task"))
    manager = ContextManager(policy=ContextPolicy(recent_turns=1, minimum_recent_turns=1))
    manager.set_active_skills((_skill("testing", "Use TDD."),))
    manager.set_workspace_memories(
        (ContextMemory("11111111", "command", "test.command", "pytest"),)
    )
    manager.set_recalled_history(
        (RecallEntry("aaaaaaaaaaaa", "tool", "old failure", 3, NOW, 10),)
    )
    summary = SummaryState("persistent work", 1, NOW)

    view = manager.build(history, summary=summary, summary_updated=True)
    contents = [message.content or "" for message in view]

    assert contents[0] == "core"
    assert "Active Skill: testing" in contents[1]
    assert contents[2] == "original task"
    assert "Workspace memory" in contents[3]
    assert "Conversation summary" in contents[4]
    assert "Recalled history" in contents[5]
    assert contents[6] == "latest task"
    report = manager.last_report
    assert report.final_context_chars > 0
    assert report.skills_included == ("testing",)
    assert report.memory_ids_included == ("11111111",)
    assert report.memory_ids_dropped == ()
    assert report.summary_used is True and report.summary_updated is True
    assert report.recall_session_ids == ("aaaaaaaaaaaa",)
    assert report.recall_entries_included == 1
    assert "persistent work" not in repr(report)
    assert "old failure" not in repr(report)


def test_pressure_reduces_recall_then_memory_and_preserves_minimum_complete_turns() -> None:
    history = ConversationHistory("core", "original")
    history.append(Message(Role.ASSISTANT, "initial"))
    for index in range(3):
        history.append(Message(Role.USER, f"turn {index}"))
        history.append(Message(Role.ASSISTANT, "R" * 150))
    manager = ContextManager(
        policy=ContextPolicy(
            max_context_chars=1_500,
            memory_chars=800,
            recall_chars=800,
            recent_turns=3,
            minimum_recent_turns=2,
        )
    )
    manager.set_workspace_memories(
        (
            ContextMemory("11111111", "constraint", "constraint.vendor", "M" * 120),
            ContextMemory("22222222", "fact", "ordinary.fact", "N" * 120),
        )
    )
    manager.set_recalled_history(
        (
            RecallEntry("aaaaaaaaaaaa", "user", "A" * 150, 1, NOW, 20),
            RecallEntry("bbbbbbbbbbbb", "tool", "B" * 150, 2, NOW, 10),
        )
    )

    view = manager.build(history)
    rendered = "\n".join(message.content or "" for message in view)
    report = manager.last_report

    assert "turn 1" in rendered and "turn 2" in rendered
    assert report.turns_dropped >= 1
    assert report.recall_entries_dropped >= 1
    assert report.memory_ids_included == ("11111111", "22222222")


def test_report_counts_l1_l2_without_recording_tool_payloads() -> None:
    history = ConversationHistory("core", "original")
    for call_id, output in (("old", "SECRET-OLD"), ("new", "X" * 500)):
        history.append(Message(Role.USER, call_id))
        history.append(
            Message(
                Role.ASSISTANT,
                tool_calls=(
                    ToolCall(call_id, "read_file", '{"path":"parser.py"}'),
                ),
            )
        )
        history.append(
            Message(
                Role.TOOL,
                ToolResult(call_id, "read_file", True, output).as_message_content(),
                tool_call_id=call_id,
            )
        )
        history.append(Message(Role.ASSISTANT, "done"))
    manager = ContextManager(
        policy=ContextPolicy(
            max_tool_output_chars=100,
            recent_turns=2,
            minimum_recent_turns=1,
        )
    )

    manager.build(history)
    report = manager.last_report

    assert report.stale_results_pruned == 1
    assert report.tool_results_truncated == 1
    assert "SECRET-OLD" not in repr(report)
    assert "parser.py" not in repr(report)


def test_agent_reports_incremental_summary_update_from_control_plane() -> None:
    history = ConversationHistory("core", "original")
    history.append(Message(Role.ASSISTANT, "old " + "x" * 300))
    history.append(Message(Role.USER, "recent"))
    history.append(Message(Role.ASSISTANT, "answer"))
    policy = ContextPolicy(
        max_context_chars=2_000,
        recent_turns=1,
        minimum_recent_turns=1,
        summary_trigger_chars=100,
    )
    context = ContextManager(policy=policy)
    runner = AgentRunner(
        FakeModelClient([ModelTurn("done")]),
        ToolRegistry(),
        context,
        summary_manager=SummaryManager(
            FakeModelClient([ModelTurn("summary")]),
            threshold_chars=policy.summary_trigger_chars,
            recent_turns=policy.recent_turns,
            max_summary_chars=policy.summary_chars,
            clock=lambda: NOW,
        ),
    )

    runner.run_turn(history, "continue")

    assert runner.summary_state is not None
    assert context.last_report.summary_used is True
    assert context.last_report.summary_updated is True


def test_report_distinguishes_summary_updated_from_summary_used() -> None:
    history = ConversationHistory("core", "original")
    manager = ContextManager(
        policy=ContextPolicy(
            max_context_chars=300,
            summary_chars=1_000,
            recent_turns=1,
            minimum_recent_turns=1,
        )
    )

    manager.build(
        history,
        summary=SummaryState("S" * 800, 1, NOW),
        summary_updated=True,
    )

    assert manager.last_report.summary_used is False
    assert manager.last_report.summary_updated is True

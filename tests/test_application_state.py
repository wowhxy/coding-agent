from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.application.events import ActivityStatus
from coding_agent.application.state import (
    ActivityView,
    AgentState,
    ChangeStatus,
    ChangeView,
    ConversationItem,
    ConversationKind,
    ProductSnapshot,
    ProductStatus,
    SessionView,
    VerificationView,
)


def test_product_snapshot_exposes_only_immutable_product_views(tmp_path: Path) -> None:
    updated = datetime(2026, 8, 29, tzinfo=timezone.utc)
    status = ProductStatus(
        provider="deepseek",
        model="deepseek-v4-flash",
        workspace=tmp_path,
        session_id="111111111111",
        agent_state=AgentState.READY,
        context_chars=120,
        context_limit=1_000,
        summary_active=True,
        memory_count=2,
        active_skills=("tdd",),
        enabled_plugins=("git-readonly",),
        active_subagents=0,
    )
    snapshot = ProductSnapshot(
        status=status,
        sessions=(SessionView("111111111111", "parser fix", updated, True, False, None),),
        conversation=(ConversationItem("m1", ConversationKind.USER, "fix it"),),
        activities=(ActivityView("a1", "tool", "pytest", "3 passed", ActivityStatus.SUCCEEDED, 1, True),),
        changes=(ChangeView("parser.py", ChangeStatus.MODIFIED, 1, 1, "@@"),),
        verifications=(VerificationView("pytest -q", True, "3 passed", "exit 0"),),
    )

    assert snapshot.status.context_percent == 12
    assert snapshot.sessions[0].display_name == "parser fix"
    assert snapshot.changes[0].status is ChangeStatus.MODIFIED


def test_status_rejects_impossible_context_and_subagent_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="context"):
        ProductStatus(
            "p", "m", tmp_path, "111111111111", AgentState.READY,
            2, 1, False, 0, (), (), 0,
        )
    with pytest.raises(ValueError, match="subagent"):
        ProductStatus(
            "p", "m", tmp_path, "111111111111", AgentState.READY,
            0, 1, False, 0, (), (), -1,
        )


def test_unnamed_session_uses_short_identifier_as_secondary_fallback() -> None:
    session = SessionView(
        "123456789abc",
        None,
        datetime(2026, 8, 29, tzinfo=timezone.utc),
        False,
        False,
        None,
    )

    assert session.display_name == "Session 123456"


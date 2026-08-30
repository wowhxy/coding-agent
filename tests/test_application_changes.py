from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from coding_agent.application.changes import (
    activity_views,
    compare_snapshots,
    snapshot_workspace,
    verification_views,
)
from coding_agent.application.events import ActivityStatus
from coding_agent.application.state import ChangeStatus
from coding_agent.protocol import Message, Role, ToolCall, ToolResult


def test_snapshots_report_deterministic_added_modified_deleted_diffs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("old\n", encoding="utf-8")
    (workspace / "gone.py").write_text("gone\n", encoding="utf-8")
    before = snapshot_workspace(workspace)

    (workspace / "a.py").write_text("new\nextra\n", encoding="utf-8")
    (workspace / "gone.py").unlink()
    (workspace / "z.py").write_text("added\n", encoding="utf-8")
    after = snapshot_workspace(workspace)

    changes = compare_snapshots(before, after)

    assert tuple(item.path for item in changes) == ("a.py", "gone.py", "z.py")
    assert tuple(item.status for item in changes) == (
        ChangeStatus.MODIFIED,
        ChangeStatus.DELETED,
        ChangeStatus.ADDED,
    )
    assert (changes[0].additions, changes[0].deletions) == (2, 1)
    assert "-old" in changes[0].diff
    assert "+new" in changes[0].diff


def test_snapshot_skips_symlinks_git_caches_and_bounds_large_or_binary_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("outside secret", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("private", encoding="utf-8")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "x.pyc").write_bytes(b"cache")
    (workspace / "binary.bin").write_bytes(b"\x00\x01\x02")
    (workspace / "large.txt").write_text("x" * 101, encoding="utf-8")
    try:
        os.symlink(outside, workspace / "linked.txt")
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    snapshot = snapshot_workspace(workspace, max_file_chars=100)
    files = {item.path: item for item in snapshot.files}

    assert set(files) == {"binary.bin", "large.txt"}
    assert files["binary.bin"].text is None
    assert files["large.txt"].text is None
    assert all(item.digest for item in files.values())


def test_snapshot_rejects_mismatched_roots_and_redacts_diff_values(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.txt").write_text("before", encoding="utf-8")
    (second / "a.txt").write_text("after", encoding="utf-8")

    with pytest.raises(ValueError, match="same workspace"):
        compare_snapshots(snapshot_workspace(first), snapshot_workspace(second))

    before = snapshot_workspace(first)
    secret = "provider-secret-value"
    (first / "a.txt").write_text(secret, encoding="utf-8")
    changes = compare_snapshots(before, snapshot_workspace(first), sensitive_values=(secret,))

    assert secret not in changes[0].diff
    assert "[REDACTED]" in changes[0].diff


def test_diff_output_is_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "many.txt"
    target.write_text("\n".join(f"old-{index}" for index in range(100)), encoding="utf-8")
    before = snapshot_workspace(workspace)
    target.write_text("\n".join(f"new-{index}" for index in range(100)), encoding="utf-8")

    change = compare_snapshots(before, snapshot_workspace(workspace), max_diff_chars=120)[0]

    assert len(change.diff) <= 120
    assert "truncated" in change.diff


def test_oversized_file_tail_change_is_still_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "large.txt"
    target.write_text("a" * 100 + "old", encoding="utf-8")
    before = snapshot_workspace(workspace, max_file_chars=50)
    target.write_text("a" * 100 + "new", encoding="utf-8")

    changes = compare_snapshots(
        before,
        snapshot_workspace(workspace, max_file_chars=50),
    )

    assert len(changes) == 1
    assert changes[0].path == "large.txt"
    assert changes[0].diff == "[binary or oversized content changed]"


def _tool_messages(call: ToolCall, result: ToolResult) -> tuple[Message, ...]:
    return (
        Message(Role.USER, "fix tests"),
        Message(Role.ASSISTANT, None, (call,)),
        Message(Role.TOOL, result.as_message_content(), tool_call_id=call.id),
        Message(Role.ASSISTANT, "Everything is fixed."),
    )


def test_command_tool_result_becomes_actual_verification_evidence() -> None:
    call = ToolCall("c1", "execute_command", json.dumps({"command": "pytest -q"}))
    result = ToolResult(
        "c1",
        "execute_command",
        True,
        "exit_code: 0\nstdout:\n42 passed in 1.8s\nstderr:\n",
    )

    views = verification_views(_tool_messages(call, result))

    assert len(views) == 1
    assert views[0].command == "pytest -q"
    assert views[0].ok is True
    assert views[0].summary == "42 passed in 1.8s"
    assert "Everything is fixed" not in views[0].detail


def test_failed_command_is_visible_as_failed_verification() -> None:
    call = ToolCall("c1", "execute_command", '{"command":"python -m pytest -q"}')
    result = ToolResult(
        "c1",
        "execute_command",
        False,
        "exit_code: 1\nstdout:\n2 failed, 40 passed\nstderr:\n",
        "COMMAND_FAILED",
        "command exited with status 1",
    )

    view = verification_views(_tool_messages(call, result))[0]

    assert view.ok is False
    assert view.summary == "2 failed, 40 passed"
    assert "COMMAND_FAILED" in view.detail


def test_tool_activity_uses_protocol_pairs_and_tolerates_malformed_payloads() -> None:
    read = ToolCall("r1", "read_file", '{"path":"src/parser.py"}')
    malformed = ToolCall("bad", "search_text", "not-json")
    messages = (
        Message(Role.ASSISTANT, None, (read, malformed)),
        Message(
            Role.TOOL,
            ToolResult("r1", "read_file", True, "source text").as_message_content(),
            tool_call_id="r1",
        ),
        Message(Role.TOOL, "not-json", tool_call_id="bad"),
    )

    activities = activity_views(messages)

    assert tuple(item.title for item in activities) == ("read_file", "search_text")
    assert activities[0].status is ActivityStatus.SUCCEEDED
    assert "src/parser.py" in activities[0].detail
    assert activities[1].status is ActivityStatus.FAILED
    assert "malformed" in activities[1].detail

from __future__ import annotations

import json
from pathlib import Path

import pytest

import coding_agent.tools.files as file_tools
from coding_agent.protocol import ToolCall, ToolResult
from coding_agent.tools.files import (
    create_replace_in_file_tool,
    create_write_file_tool,
)
from coding_agent.tools.paths import WorkspacePaths
from coding_agent.tools.registry import ToolRegistry


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def _dispatch_write(root: Path, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    registry.register(create_write_file_tool(WorkspacePaths(root)))
    return registry.dispatch(
        ToolCall("write-1", "write_file", json.dumps(arguments))
    )


def _dispatch_replace(root: Path, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    registry.register(create_replace_in_file_tool(WorkspacePaths(root)))
    return registry.dispatch(
        ToolCall("replace-1", "replace_in_file", json.dumps(arguments))
    )


def test_write_file_creates_exact_utf8_content(tmp_path: Path) -> None:
    (tmp_path / "package").mkdir()
    result = _dispatch_write(
        tmp_path,
        {"path": "package/new.py", "content": "第一行\nsecond\r\n"},
    )

    assert result.ok is True
    assert result.output == "created file: package/new.py"
    assert result.error_code is None
    assert result.error_message is None
    assert (tmp_path / "package" / "new.py").read_bytes() == (
        "第一行\nsecond\r\n".encode("utf-8")
    )


@pytest.mark.parametrize("overwrite", [None, False])
def test_write_file_rejects_existing_file_without_overwrite(
    tmp_path: Path, overwrite: bool | None
) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")
    arguments: dict[str, object] = {"path": "existing.txt", "content": "new"}
    if overwrite is not None:
        arguments["overwrite"] = overwrite

    result = _dispatch_write(tmp_path, arguments)

    assert result.ok is False
    assert result.error_code == "FILE_ALREADY_EXISTS"
    assert result.error_message
    assert target.read_text(encoding="utf-8") == "original"


def test_write_file_overwrite_replaces_exact_content(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")

    result = _dispatch_write(
        tmp_path,
        {"path": "existing.txt", "content": "replacement\n", "overwrite": True},
    )

    assert result.ok is True
    assert result.output == "overwrote file: existing.txt"
    assert target.read_bytes() == b"replacement\n"


def test_write_file_does_not_create_a_missing_parent(tmp_path: Path) -> None:
    result = _dispatch_write(
        tmp_path,
        {"path": "missing/new.txt", "content": "text"},
    )

    assert result.ok is False
    assert result.error_code == "FILE_NOT_FOUND"
    assert result.error_message
    assert not (tmp_path / "missing").exists()


def test_write_file_rejects_parent_escape(tmp_path: Path) -> None:
    result = _dispatch_write(
        tmp_path,
        {"path": "../outside.txt", "content": "secret"},
    )

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert result.error_message


def test_write_file_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "link.txt"
    _symlink_or_skip(link, outside, is_directory=False)

    result = _dispatch_write(
        workspace,
        {"path": "link.txt", "content": "replacement", "overwrite": True},
    )

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "new.txt", "content": "text", "overwrite": 1},
        {"path": "new.txt", "content": "text", "extra": True},
    ],
)
def test_write_file_rejects_invalid_or_unknown_arguments(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    result = _dispatch_write(tmp_path, arguments)

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message
    assert not (tmp_path / "new.txt").exists()


def test_failed_atomic_overwrite_leaves_original_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("original", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(file_tools.os, "replace", fail_replace)

    result = _dispatch_write(
        tmp_path,
        {"path": "target.txt", "content": "new", "overwrite": True},
    )

    assert result.ok is False
    assert result.error_code == "TOOL_INTERNAL_ERROR"
    assert target.read_text(encoding="utf-8") == "original"
    assert [path.name for path in tmp_path.iterdir()] == ["target.txt"]


def test_replace_requires_exactly_one_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = _dispatch_replace(
        tmp_path,
        {"path": "module.py", "old_text": "value = 1", "new_text": "value = 2"},
    )

    assert result.ok is False
    assert result.error_code == "EDIT_TARGET_AMBIGUOUS"
    assert result.error_message
    assert target.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_replace_rejects_missing_occurrence_without_changing_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    result = _dispatch_replace(
        tmp_path,
        {"path": "module.py", "old_text": "missing", "new_text": "replacement"},
    )

    assert result.ok is False
    assert result.error_code == "EDIT_TARGET_NOT_FOUND"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_replace_changes_exactly_one_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    result = _dispatch_replace(
        tmp_path,
        {"path": "module.py", "old_text": "value = 1", "new_text": "value = 2"},
    )

    assert result.ok is True
    assert result.output == "replaced 1 occurrence in: module.py"
    assert result.error_code is None
    assert result.error_message is None
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_replace_rejects_non_utf8_file(tmp_path: Path) -> None:
    target = tmp_path / "binary.dat"
    target.write_bytes(b"old\xff")

    result = _dispatch_replace(
        tmp_path,
        {"path": "binary.dat", "old_text": "old", "new_text": "new"},
    )

    assert result.ok is False
    assert result.error_code == "DECODE_ERROR"
    assert result.error_message
    assert target.read_bytes() == b"old\xff"


def test_replace_rejects_path_escape(tmp_path: Path) -> None:
    result = _dispatch_replace(
        tmp_path,
        {"path": "../outside.txt", "old_text": "old", "new_text": "new"},
    )

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "module.py", "old_text": "", "new_text": "new"},
        {"path": "module.py", "old_text": "old", "new_text": "new", "extra": 1},
    ],
)
def test_replace_rejects_empty_target_and_unknown_fields(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    target = tmp_path / "module.py"
    target.write_text("old", encoding="utf-8")

    result = _dispatch_replace(tmp_path, arguments)

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert target.read_text(encoding="utf-8") == "old"


def test_failed_atomic_replacement_leaves_original_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "module.py"
    target.write_text("old value", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(file_tools.os, "replace", fail_replace)

    result = _dispatch_replace(
        tmp_path,
        {"path": "module.py", "old_text": "old", "new_text": "new"},
    )

    assert result.ok is False
    assert result.error_code == "TOOL_INTERNAL_ERROR"
    assert target.read_text(encoding="utf-8") == "old value"
    assert [path.name for path in tmp_path.iterdir()] == ["module.py"]

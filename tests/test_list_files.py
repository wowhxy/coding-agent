from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.protocol import ToolCall, ToolResult
from coding_agent.tools.files import create_list_files_tool
from coding_agent.tools.paths import WorkspacePathError, WorkspacePaths
from coding_agent.tools.registry import ToolRegistry


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def _dispatch_list(root: Path, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    registry.register(create_list_files_tool(WorkspacePaths(root)))
    return registry.dispatch(
        ToolCall("list-1", "list_files", json.dumps(arguments))
    )


def test_workspace_root_remains_resolved_after_working_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside.txt"
    target.write_text("inside", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    paths = WorkspacePaths(Path("workspace"))

    monkeypatch.chdir(elsewhere)

    assert paths.root == workspace.resolve()
    assert paths.resolve_existing("inside.txt") == target.resolve()


def test_workspace_resolves_normal_relative_file_and_displays_posix_path(
    tmp_path: Path,
) -> None:
    target = tmp_path / "package" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1", encoding="utf-8")
    paths = WorkspacePaths(tmp_path)

    resolved = paths.resolve_existing("package/module.py")

    assert resolved == target.resolve()
    assert paths.display_path(resolved) == "package/module.py"


def test_absolute_path_is_rejected_without_exposing_it_in_the_message(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths(tmp_path)
    outside = (tmp_path.parent / "outside.txt").resolve()

    with pytest.raises(WorkspacePathError) as caught:
        paths.resolve_existing(outside)

    assert caught.value.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert str(caught.value)
    assert str(outside) not in str(caught.value)


def test_parent_escape_is_rejected(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path)

    with pytest.raises(WorkspacePathError) as caught:
        paths.resolve_existing("../outside.txt")

    assert caught.value.error_code == "PATH_OUTSIDE_WORKSPACE"


def test_existing_symlink_to_outside_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.txt"
    _symlink_or_skip(link, outside, is_directory=False)

    with pytest.raises(WorkspacePathError) as caught:
        WorkspacePaths(workspace).resolve_existing("link.txt")

    assert caught.value.error_code == "PATH_OUTSIDE_WORKSPACE"


def test_new_file_under_symlinked_outside_parent_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "linked-directory"
    _symlink_or_skip(link, outside, is_directory=True)

    with pytest.raises(WorkspacePathError) as caught:
        WorkspacePaths(workspace).resolve_new_file("linked-directory/new.txt")

    assert caught.value.error_code == "PATH_OUTSIDE_WORKSPACE"


def test_list_files_recurses_and_sorts_exact_relative_output(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "z.py").write_text("z", encoding="utf-8")
    (package / "a.py").write_text("a", encoding="utf-8")

    result = _dispatch_list(tmp_path, {})

    assert result.ok is True
    assert result.output == "\n".join(
        [
            "[D] package/",
            "[F] package/a.py",
            "[F] package/z.py",
        ]
    )


def test_list_files_ignores_fixed_generated_directories(tmp_path: Path) -> None:
    for name in (".git", ".venv", "node_modules", "__pycache__"):
        ignored = tmp_path / name
        ignored.mkdir()
        (ignored / "hidden.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")

    result = _dispatch_list(tmp_path, {"path": "."})

    assert result.ok is True
    assert result.output == "[F] visible.txt"


def test_list_files_caps_entries_at_500_with_visible_marker(tmp_path: Path) -> None:
    for index in range(501):
        (tmp_path / f"file_{index:04}.txt").write_text("x", encoding="utf-8")

    result = _dispatch_list(tmp_path, {})

    lines = result.output.splitlines()
    entries = [line for line in lines if line.startswith("[") and "truncated" not in line]
    assert len(entries) == 500
    assert entries[0] == "[F] file_0000.txt"
    assert entries[-1] == "[F] file_0499.txt"
    assert "truncated" in lines[-1]


def test_list_files_returns_file_not_found(tmp_path: Path) -> None:
    result = _dispatch_list(tmp_path, {"path": "missing"})

    assert result.ok is False
    assert result.error_code == "FILE_NOT_FOUND"
    assert result.error_message


def test_list_files_rejects_file_instead_of_directory(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("text", encoding="utf-8")

    result = _dispatch_list(tmp_path, {"path": "file.txt"})

    assert result.ok is False
    assert result.error_code == "NOT_A_FILE"
    assert result.error_message


def test_list_files_rejects_unknown_argument(tmp_path: Path) -> None:
    result = _dispatch_list(tmp_path, {"extra": True})

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"

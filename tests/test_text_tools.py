from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.protocol import ToolCall, ToolResult
from coding_agent.tools.files import create_read_file_tool, create_search_text_tool
from coding_agent.tools.paths import WorkspacePaths
from coding_agent.tools.registry import ToolRegistry


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def _dispatch_search(root: Path, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    registry.register(create_search_text_tool(WorkspacePaths(root)))
    return registry.dispatch(
        ToolCall("search-1", "search_text", json.dumps(arguments))
    )


def _dispatch_read(root: Path, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    registry.register(create_read_file_tool(WorkspacePaths(root)))
    return registry.dispatch(
        ToolCall("read-1", "read_file", json.dumps(arguments))
    )


def test_search_single_file_is_literal_and_case_sensitive(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text(
        "Alpha needle\nneedle lower\nNeedle upper\n",
        encoding="utf-8",
    )

    result = _dispatch_search(
        tmp_path,
        {"path": "sample.txt", "query": "needle"},
    )

    assert result.ok is True
    assert result.output == "\n".join(
        [
            "sample.txt:1:Alpha needle",
            "sample.txt:2:needle lower",
        ]
    )


def test_search_directory_returns_stable_path_and_line_order(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "z.py").write_text("match z\n", encoding="utf-8")
    (package / "a.py").write_text("none\nmatch a2\nmatch a3\n", encoding="utf-8")

    result = _dispatch_search(tmp_path, {"path": ".", "query": "match"})

    assert result.ok is True
    assert result.output == "\n".join(
        [
            "package/a.py:2:match a2",
            "package/a.py:3:match a3",
            "package/z.py:1:match z",
        ]
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": ".", "query": ""},
        {"path": ".", "query": "x", "extra": True},
    ],
)
def test_search_rejects_empty_query_and_unknown_fields(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    result = _dispatch_search(tmp_path, arguments)

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message


def test_directory_search_skips_ignored_binary_oversized_and_invalid_files(
    tmp_path: Path,
) -> None:
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "ignored.txt").write_text("needle ignored", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"needle\x00binary")
    (tmp_path / "oversized.txt").write_bytes(b"needle" + b"x" * (1024 * 1024))
    (tmp_path / "invalid.txt").write_bytes(b"needle\xff")
    (tmp_path / "visible.txt").write_text("needle visible", encoding="utf-8")

    result = _dispatch_search(tmp_path, {"path": ".", "query": "needle"})

    assert result.ok is True
    assert result.output == "visible.txt:1:needle visible"


def test_directory_search_skips_file_and_directory_symlinks(tmp_path: Path) -> None:
    search_root = tmp_path / "search"
    search_root.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()
    file_target = targets / "file.txt"
    file_target.write_text("unique-file-match", encoding="utf-8")
    directory_target = targets / "directory"
    directory_target.mkdir()
    (directory_target / "deep.txt").write_text(
        "unique-directory-match", encoding="utf-8"
    )
    _symlink_or_skip(search_root / "file-link.txt", file_target, is_directory=False)
    _symlink_or_skip(search_root / "directory-link", directory_target, is_directory=True)

    result = _dispatch_search(tmp_path, {"path": "search", "query": "unique-"})

    assert result.ok is True
    assert result.output == ""


def test_directory_search_does_not_read_outside_file_through_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    search_root = workspace / "search"
    search_root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-unique-literal", encoding="utf-8")
    link = search_root / "outside-link.txt"
    _symlink_or_skip(link, outside, is_directory=False)

    result = _dispatch_search(
        workspace,
        {"path": "search", "query": "outside-unique-literal"},
    )

    assert result.ok is True
    assert "outside-unique-literal" not in result.output
    assert "outside-link.txt" not in result.output


def test_search_returns_at_most_100_matches_with_marker(tmp_path: Path) -> None:
    (tmp_path / "many.txt").write_text(
        "\n".join(f"needle {index}" for index in range(101)),
        encoding="utf-8",
    )

    result = _dispatch_search(tmp_path, {"path": ".", "query": "needle"})

    lines = result.output.splitlines()
    assert len([line for line in lines if line.startswith("many.txt:")]) == 100
    assert lines[0] == "many.txt:1:needle 0"
    assert lines[99] == "many.txt:100:needle 99"
    assert "truncated" in lines[-1]


def test_search_output_is_bounded_to_20000_characters(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("needle" + "x" * 21_000, encoding="utf-8")

    result = _dispatch_search(tmp_path, {"path": "long.txt", "query": "needle"})

    assert result.ok is True
    assert len(result.output) == 20_000
    assert "output truncated" in result.output


def test_search_missing_file_returns_recoverable_error(tmp_path: Path) -> None:
    result = _dispatch_search(tmp_path, {"path": "missing", "query": "x"})

    assert result.ok is False
    assert result.error_code == "FILE_NOT_FOUND"
    assert result.error_message


def test_search_invalid_utf8_single_file_returns_decode_error(tmp_path: Path) -> None:
    (tmp_path / "invalid.bin").write_bytes(b"text\xff")

    result = _dispatch_search(
        tmp_path,
        {"path": "invalid.bin", "query": "text"},
    )

    assert result.ok is False
    assert result.error_code == "DECODE_ERROR"
    assert result.error_message


def test_search_rejects_path_escape(tmp_path: Path) -> None:
    result = _dispatch_search(tmp_path, {"path": "../outside", "query": "x"})

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert result.error_message


def test_read_file_uses_one_based_inclusive_lines(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = _dispatch_read(
        tmp_path,
        {"path": "sample.py", "start_line": 2, "end_line": 3},
    )

    assert result.ok is True
    assert result.output == "2: two\n3: three"


def test_read_file_defaults_to_the_complete_numbered_file(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = _dispatch_read(tmp_path, {"path": "sample.txt"})

    assert result.ok is True
    assert result.output == "1: one\n2: two"


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "sample.txt", "start_line": 0},
        {"path": "sample.txt", "end_line": 0},
        {"path": "sample.txt", "start_line": 3, "end_line": 2},
        {"path": "sample.txt", "start_line": True},
        {"path": "sample.txt", "end_line": False},
    ],
)
def test_read_file_rejects_invalid_line_ranges(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = _dispatch_read(tmp_path, arguments)

    assert result.ok is False
    assert result.error_code == "MALFORMED_ARGUMENTS"
    assert result.error_message


def test_read_file_start_beyond_eof_returns_empty_output(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = _dispatch_read(
        tmp_path,
        {"path": "sample.txt", "start_line": 10},
    )

    assert result.ok is True
    assert result.output == ""


@pytest.mark.parametrize(
    ("path", "setup", "error_code"),
    [
        ("missing.txt", "missing", "FILE_NOT_FOUND"),
        ("directory", "directory", "NOT_A_FILE"),
        ("invalid.bin", "invalid", "DECODE_ERROR"),
        ("../outside.txt", "escape", "PATH_OUTSIDE_WORKSPACE"),
    ],
)
def test_read_file_returns_structured_file_errors(
    tmp_path: Path, path: str, setup: str, error_code: str
) -> None:
    if setup == "directory":
        (tmp_path / path).mkdir()
    elif setup == "invalid":
        (tmp_path / path).write_bytes(b"text\xff")

    result = _dispatch_read(tmp_path, {"path": path})

    assert result.ok is False
    assert result.error_code == error_code
    assert result.error_message


def test_read_file_output_is_bounded_to_20000_characters(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("x" * 21_000, encoding="utf-8")

    result = _dispatch_read(tmp_path, {"path": "long.txt"})

    assert result.ok is True
    assert len(result.output) == 20_000
    assert "output truncated" in result.output

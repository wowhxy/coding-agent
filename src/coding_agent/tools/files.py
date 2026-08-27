"""Local file tools with deterministic output and workspace containment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..context import truncate_text
from ..protocol import ToolDefinition, ToolResult
from .paths import WorkspacePathError, WorkspacePaths
from .registry import RegisteredTool, ToolArgumentError, require_keys


IGNORED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__"}
)
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_FILE_BYTES = 1024 * 1024
MAX_TEXT_OUTPUT_CHARS = 20_000
SearchMatch = tuple[str, int, str]


def create_list_files_tool(paths: WorkspacePaths) -> RegisteredTool:
    """Create a recursively listing tool bound to one workspace."""

    definition = ToolDefinition(
        name="list_files",
        description="Recursively list files and directories inside the workspace.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "additionalProperties": False,
        },
    )

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required=(), optional={"path"})
        requested_path = arguments.get("path", ".")
        if not isinstance(requested_path, str):
            raise ToolArgumentError("path must be a string")
        return {"path": requested_path}

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            target = paths.resolve_existing(arguments["path"])
        except WorkspacePathError as exc:
            return _path_failure(call_id, "list_files", exc)
        if not target.is_dir():
            return ToolResult(
                call_id,
                "list_files",
                False,
                "",
                "NOT_A_FILE",
                f"path is not a directory: {arguments['path']}",
            )

        entries = _list_entries(paths, target)
        visible_entries = entries[:MAX_LIST_ENTRIES]
        if len(entries) > MAX_LIST_ENTRIES:
            remaining = len(entries) - MAX_LIST_ENTRIES
            visible_entries.append(
                f"[output truncated: {remaining} additional entries]"
            )
        return ToolResult(
            call_id,
            "list_files",
            True,
            "\n".join(visible_entries),
        )

    return RegisteredTool(definition, validate, handle)


def create_search_text_tool(paths: WorkspacePaths) -> RegisteredTool:
    """Create a literal, case-sensitive text search tool."""

    definition = ToolDefinition(
        name="search_text",
        description="Search for literal text in a workspace file or directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required={"query"}, optional={"path"})
        requested_path = arguments.get("path", ".")
        query = arguments["query"]
        if not isinstance(requested_path, str):
            raise ToolArgumentError("path must be a string")
        if not isinstance(query, str):
            raise ToolArgumentError("query must be a string")
        if query == "":
            raise ToolArgumentError("query must not be empty")
        return {"path": requested_path, "query": query}

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            target = paths.resolve_existing(arguments["path"])
        except WorkspacePathError as exc:
            return _path_failure(call_id, "search_text", exc)

        if target.is_file():
            try:
                text = _read_single_search_file(target)
            except UnicodeDecodeError:
                return _decode_failure(
                    call_id,
                    "search_text",
                    arguments["path"],
                )
            matches = _matches_in_text(
                paths.display_path(target),
                text,
                arguments["query"],
            )
        elif target.is_dir():
            matches = _search_directory(paths, target, arguments["query"])
        else:
            return ToolResult(
                call_id,
                "search_text",
                False,
                "",
                "NOT_A_FILE",
                f"path is neither a file nor a directory: {arguments['path']}",
            )

        output_lines = [
            _render_match(match) for match in matches[:MAX_SEARCH_MATCHES]
        ]
        if len(matches) > MAX_SEARCH_MATCHES:
            output_lines.append(
                "[output truncated: more than 100 matches]"
            )
        output = truncate_text("\n".join(output_lines), MAX_TEXT_OUTPUT_CHARS)
        return ToolResult(call_id, "search_text", True, output)

    return RegisteredTool(definition, validate, handle)


def create_read_file_tool(paths: WorkspacePaths) -> RegisteredTool:
    """Create a UTF-8 text reader with one-based inclusive line ranges."""

    definition = ToolDefinition(
        name="read_file",
        description="Read a UTF-8 workspace file with numbered lines.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(
            arguments,
            required={"path"},
            optional={"start_line", "end_line"},
        )
        requested_path = arguments["path"]
        start_line = arguments.get("start_line", 1)
        end_line = arguments.get("end_line")
        if not isinstance(requested_path, str):
            raise ToolArgumentError("path must be a string")
        if not _is_line_number(start_line):
            raise ToolArgumentError("start_line must be a positive integer")
        if end_line is not None and not _is_line_number(end_line):
            raise ToolArgumentError("end_line must be a positive integer")
        if end_line is not None and end_line < start_line:
            raise ToolArgumentError("end_line must be greater than or equal to start_line")
        return {
            "path": requested_path,
            "start_line": start_line,
            "end_line": end_line,
        }

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            target = paths.resolve_existing(arguments["path"])
        except WorkspacePathError as exc:
            return _path_failure(call_id, "read_file", exc)
        if not target.is_file():
            return ToolResult(
                call_id,
                "read_file",
                False,
                "",
                "NOT_A_FILE",
                f"path is not a file: {arguments['path']}",
            )

        try:
            text = target.read_bytes().decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _decode_failure(call_id, "read_file", arguments["path"])

        lines = text.splitlines()
        start_line = arguments["start_line"]
        end_line = arguments["end_line"]
        selected = lines[start_line - 1 : end_line]
        output = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start_line)
        )
        output = truncate_text(output, MAX_TEXT_OUTPUT_CHARS)
        return ToolResult(call_id, "read_file", True, output)

    return RegisteredTool(definition, validate, handle)


def _list_entries(paths: WorkspacePaths, target: Path) -> list[str]:
    entries: list[tuple[str, bool]] = []
    for current, dirnames, filenames in os.walk(target, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if name not in IGNORED_DIRECTORIES
        )
        for name in dirnames:
            display = paths.display_path(current_path / name)
            entries.append((display, True))
        for name in sorted(filenames):
            display = paths.display_path(current_path / name)
            entries.append((display, False))

    entries.sort(key=lambda entry: entry[0])
    return [
        f"[D] {display}/" if is_directory else f"[F] {display}"
        for display, is_directory in entries
    ]


def _read_single_search_file(target: Path) -> str:
    data = target.read_bytes()
    if len(data) > MAX_SEARCH_FILE_BYTES or b"\x00" in data:
        raise UnicodeDecodeError("utf-8", data, 0, 1, "not searchable UTF-8 text")
    return data.decode("utf-8", errors="strict")


def _search_directory(
    paths: WorkspacePaths,
    target: Path,
    query: str,
) -> list[SearchMatch]:
    matches: list[SearchMatch] = []
    for current, dirnames, filenames in os.walk(target, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in IGNORED_DIRECTORIES
            and not (current_path / name).is_symlink()
        )
        for name in sorted(filenames):
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            if len(data) > MAX_SEARCH_FILE_BYTES or b"\x00" in data:
                continue
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            display = paths.display_path(candidate)
            matches.extend(_matches_in_text(display, text, query))

    matches.sort(key=lambda match: (match[0], match[1]))
    return matches


def _matches_in_text(display: str, text: str, query: str) -> list[SearchMatch]:
    return [
        (display, line_number, line)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if query in line
    ]


def _render_match(match: SearchMatch) -> str:
    path, line_number, line = match
    return f"{path}:{line_number}:{line}"


def _is_line_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _decode_failure(call_id: str, tool_name: str, path: str) -> ToolResult:
    return ToolResult(
        call_id,
        tool_name,
        False,
        "",
        "DECODE_ERROR",
        f"file is not searchable UTF-8 text: {path}",
    )


def _path_failure(
    call_id: str,
    tool_name: str,
    error: WorkspacePathError,
) -> ToolResult:
    return ToolResult(
        call_id,
        tool_name,
        False,
        "",
        error.error_code,
        str(error),
    )

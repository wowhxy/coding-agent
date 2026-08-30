"""Bounded display-only workspace changes and canonical tool evidence."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ..protocol import Message, Role, ToolCall
from .events import (
    ActivitySource,
    ActivityStatus,
    classify_tool_activity,
    redact_product_text,
)
from .state import ActivityView, ChangeStatus, ChangeView, VerificationView


_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".superpowers",
        "__pycache__",
        "node_modules",
    }
)
_VERIFY_PATTERN = re.compile(
    r"(?:^|\s)(?:pytest|ctest|unittest|ruff|mypy|pyright|compileall|"
    r"py_compile|cargo\s+test|go\s+test|npm\s+(?:run\s+)?test|"
    r"pnpm\s+(?:run\s+)?test|yarn\s+test|mvn\s+test|gradle\s+test|"
    r"dotnet\s+test)(?:\s|$)",
    re.IGNORECASE,
)
_RESULT_WORDS = re.compile(
    r"\b(?:passed|failed|errors?|success(?:ful)?|tests?)\b", re.IGNORECASE
)
_MAX_ACTIVITY_DETAIL = 4_000
_MAX_VERIFICATION_DETAIL = 8_000


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    text: str | None
    digest: str


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: Path
    files: tuple[FileSnapshot, ...]


def snapshot_workspace(
    workspace: Path,
    *,
    max_files: int = 2_000,
    max_file_chars: int = 200_000,
) -> WorkspaceSnapshot:
    """Capture bounded text state without following workspace symlinks."""

    if type(max_files) is not int or max_files <= 0:
        raise ValueError("max_files must be positive")
    if type(max_file_chars) is not int or max_file_chars <= 0:
        raise ValueError("max_file_chars must be positive")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace must be a directory")

    files: list[FileSnapshot] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _IGNORED_DIRECTORIES
            and not _is_symlink(current_path / name)
        )
        for name in sorted(file_names):
            path = current_path / name
            if _is_symlink(path):
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            snapshot = _snapshot_file(path, relative, max_file_chars)
            if snapshot is not None:
                files.append(snapshot)
            if len(files) >= max_files:
                return WorkspaceSnapshot(root, tuple(sorted(files, key=lambda item: item.path)))
    return WorkspaceSnapshot(root, tuple(sorted(files, key=lambda item: item.path)))


def compare_snapshots(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    max_diff_chars: int = 40_000,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[ChangeView, ...]:
    """Return deterministic, bounded display diffs for one workspace."""

    if before.root != after.root:
        raise ValueError("snapshots must belong to the same workspace")
    if type(max_diff_chars) is not int or max_diff_chars <= 0:
        raise ValueError("max_diff_chars must be positive")
    old_files = {item.path: item for item in before.files}
    new_files = {item.path: item for item in after.files}
    changes: list[ChangeView] = []
    for path in sorted(old_files.keys() | new_files.keys()):
        old = old_files.get(path)
        new = new_files.get(path)
        if old is not None and new is not None and old.digest == new.digest:
            continue
        if old is None:
            status = ChangeStatus.ADDED
        elif new is None:
            status = ChangeStatus.DELETED
        else:
            status = ChangeStatus.MODIFIED
        diff, additions, deletions = _file_diff(path, old, new)
        diff = redact_product_text(diff, sensitive_values)
        diff = _truncate_with_marker(diff, max_diff_chars, "[diff truncated]")
        changes.append(ChangeView(path, status, additions, deletions, diff))
    return tuple(changes)


def activity_views(
    messages: tuple[Message, ...],
    start: int = 0,
    *,
    sensitive_values: tuple[str, ...] = (),
    tool_observer: Callable[[str], tuple[str, str] | None] | None = None,
) -> tuple[ActivityView, ...]:
    """Project canonical ToolCall/ToolResult pairs into concise activities."""

    if type(start) is not int or start < 0:
        raise ValueError("activity start must be a non-negative integer")
    results = _tool_results(messages)
    activities: list[ActivityView] = []
    ordinal = 0
    for message in messages:
        if message.role is not Role.ASSISTANT:
            continue
        for call in message.tool_calls:
            if ordinal < start:
                ordinal += 1
                continue
            result = results.get(call.id)
            argument_detail = _argument_detail(call)
            if result is None:
                status = ActivityStatus.RUNNING
                result_detail = ""
            elif result is _MALFORMED:
                status = ActivityStatus.FAILED
                result_detail = "[malformed tool result]"
            else:
                assert isinstance(result, dict)
                status = (
                    ActivityStatus.SUCCEEDED
                    if result.get("ok") is True
                    else ActivityStatus.FAILED
                )
                result_detail = _result_detail(result)
            detail = "\n".join(
                part for part in (argument_detail, result_detail) if part
            )
            detail = redact_product_text(detail, sensitive_values)
            detail = _truncate_with_marker(
                detail, _MAX_ACTIVITY_DETAIL, "[activity detail truncated]"
            )
            activities.append(
                ActivityView(
                    call.id,
                    "tool",
                    call.name,
                    detail,
                    status,
                    None,
                    bool(detail),
                    *_activity_observation(call.name, tool_observer),
                )
            )
            ordinal += 1
    return tuple(activities)


def _activity_observation(
    tool_name: str,
    observer: Callable[[str], tuple[str, str] | None] | None,
) -> tuple[ActivitySource, str, str | None]:
    observation = observer(tool_name) if observer is not None else None
    source_name, activity_kind = (
        observation if observation is not None else (None, None)
    )
    source, plugin_name = classify_tool_activity(source_name, activity_kind)
    return source, tool_name, plugin_name


def verification_views(
    messages: tuple[Message, ...],
    *,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[VerificationView, ...]:
    """Return actual command evidence; assistant completion text is not evidence."""

    results = _tool_results(messages)
    views: list[VerificationView] = []
    for message in messages:
        if message.role is not Role.ASSISTANT:
            continue
        for call in message.tool_calls:
            if call.name != "execute_command":
                continue
            arguments = _json_object(call.arguments_json)
            if arguments is None or type(arguments.get("command")) is not str:
                continue
            command = arguments["command"].strip()
            if not _VERIFY_PATTERN.search(command):
                continue
            result = results.get(call.id)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                continue
            output = result.get("output") if type(result.get("output")) is str else ""
            summary = _verification_summary(output, result["ok"], result)
            detail = _result_detail(result)
            command = redact_product_text(command, sensitive_values)
            summary = redact_product_text(summary, sensitive_values)
            detail = redact_product_text(detail, sensitive_values)
            views.append(
                VerificationView(
                    command,
                    result["ok"],
                    summary,
                    _truncate_with_marker(
                        detail,
                        _MAX_VERIFICATION_DETAIL,
                        "[verification detail truncated]",
                    ),
                )
            )
    return tuple(views)


def _snapshot_file(path: Path, relative: str, limit: int) -> FileSnapshot | None:
    try:
        stat = path.stat()
        size = stat.st_size
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
            tail = b""
            if size > limit + 1:
                stream.seek(max(0, size - limit))
                tail = stream.read(limit)
    except (OSError, ValueError):
        return None
    digest = hashlib.sha256(
        data + b"\0" + tail + f"\0{size}\0{stat.st_mtime_ns}".encode("ascii")
    ).hexdigest()
    if size > limit or len(data) > limit or b"\0" in data:
        return FileSnapshot(relative, None, digest)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return FileSnapshot(relative, text, digest)


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _file_diff(
    path: str,
    old: FileSnapshot | None,
    new: FileSnapshot | None,
) -> tuple[str, int, int]:
    old_text = "" if old is None else old.text
    new_text = "" if new is None else new.text
    if old_text is None or new_text is None:
        return "[binary or oversized content changed]", 0, 0
    lines = tuple(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    additions = sum(line.startswith("+") and not line.startswith("+++") for line in lines)
    deletions = sum(line.startswith("-") and not line.startswith("---") for line in lines)
    return "".join(lines), additions, deletions


class _Malformed:
    pass


_MALFORMED = _Malformed()


def _tool_results(messages: tuple[Message, ...]) -> dict[str, dict[str, Any] | _Malformed]:
    results: dict[str, dict[str, Any] | _Malformed] = {}
    for message in messages:
        if message.role is not Role.TOOL or message.tool_call_id is None:
            continue
        parsed = _json_object(message.content or "")
        if parsed is None or type(parsed.get("ok")) is not bool:
            results[message.tool_call_id] = _MALFORMED
        else:
            results[message.tool_call_id] = parsed
    return results


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if type(value) is dict else None


def _argument_detail(call: ToolCall) -> str:
    arguments = _json_object(call.arguments_json)
    if arguments is None:
        return "[malformed tool arguments]"
    preferred = (
        "command",
        "path",
        "query",
        "directory",
        "old_text",
    )
    for key in preferred:
        value = arguments.get(key)
        if type(value) is str and value:
            if key == "old_text":
                return "exact text replacement"
            return value
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


def _result_detail(result: dict[str, Any]) -> str:
    parts: list[str] = []
    output = result.get("output")
    if type(output) is str and output:
        parts.append(output)
    code = result.get("error_code")
    message = result.get("error_message")
    if type(code) is str and code:
        parts.append(code)
    if type(message) is str and message:
        parts.append(message)
    return "\n".join(parts)


def _verification_summary(
    output: str, ok: bool, result: dict[str, Any]
) -> str:
    candidates = [
        line.strip()
        for line in output.splitlines()
        if line.strip()
        and not line.startswith("exit_code:")
        and line.strip() not in {"stdout:", "stderr:"}
    ]
    for line in candidates:
        if _RESULT_WORDS.search(line):
            return line[:500]
    if candidates:
        return candidates[-1][:500]
    error = result.get("error_message")
    if type(error) is str and error.strip():
        return error.strip()[:500]
    return "command succeeded" if ok else "command failed"


def _truncate_with_marker(text: str, limit: int, marker: str) -> str:
    if len(text) <= limit:
        return text
    decorated = f"\n{marker}"
    if len(decorated) >= limit:
        return marker[:limit]
    return text[: limit - len(decorated)] + decorated

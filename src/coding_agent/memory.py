"""Explicit per-workspace memory stored separately from session history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .context import truncate_text
from .session import SessionError
from .session_store import _atomic_write_text, _canonical_session_root, _canonical_workspace


_SCHEMA_VERSION = 1
_ITEM_ID = re.compile(r"[0-9a-f]{8}")
_MAX_ITEMS = 100
_MAX_TEXT_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    text: str
    created_at: datetime


class WorkspaceMemoryStore:
    """Atomically persist user-authored memory scoped to one workspace."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_generator: Callable[[], str] = lambda: secrets.token_hex(4),
    ) -> None:
        self.root = _canonical_session_root(root)
        self._clock = clock
        self._id_generator = id_generator

    def list(self, workspace: Path) -> tuple[MemoryItem, ...]:
        canonical = self._workspace(workspace)
        document = self._read(canonical)
        return tuple(_parse_item(item) for item in document["items"])

    def add(
        self,
        workspace: Path,
        text: str,
        sensitive_values: tuple[str, ...],
    ) -> MemoryItem:
        canonical = self._workspace(workspace)
        if type(text) is not str:
            _invalid("memory text is invalid")
        normalized = text.strip()
        for sensitive in sensitive_values:
            if type(sensitive) is str and sensitive:
                normalized = normalized.replace(sensitive, "[REDACTED]")
        if not normalized or len(normalized) > _MAX_TEXT_CHARS:
            _invalid("memory text must contain 1 to 2000 characters")
        document = self._read(canonical)
        if len(document["items"]) >= _MAX_ITEMS:
            raise SessionError("MEMORY_LIMIT", "workspace memory limit reached")
        try:
            item_id = self._id_generator()
            created_at = self._clock()
        except Exception:
            raise SessionError("MEMORY_SAVE_FAILED", "memory could not be saved") from None
        if (
            type(item_id) is not str
            or _ITEM_ID.fullmatch(item_id) is None
            or any(item["id"] == item_id for item in document["items"])
            or not _is_utc(created_at)
        ):
            raise SessionError("MEMORY_SAVE_FAILED", "memory metadata is invalid")
        item = MemoryItem(item_id, normalized, created_at)
        document["items"].append(_item_payload(item))
        self._write(canonical, document)
        return item

    def delete(self, workspace: Path, item_id: str) -> bool:
        canonical = self._workspace(workspace)
        if type(item_id) is not str or _ITEM_ID.fullmatch(item_id) is None:
            _invalid("memory id is invalid")
        document = self._read(canonical)
        remaining = [item for item in document["items"] if item["id"] != item_id]
        if len(remaining) == len(document["items"]):
            return False
        document["items"] = remaining
        self._write(canonical, document)
        return True

    def clear(self, workspace: Path) -> None:
        canonical = self._workspace(workspace)
        document = self._read(canonical)
        document["items"] = []
        self._write(canonical, document)

    def render(self, workspace: Path, max_chars: int = 8_000) -> str:
        if max_chars <= 0:
            raise ValueError("memory render limit must be positive")
        text = "\n".join(f"[{item.id}] {item.text}" for item in self.list(workspace))
        return truncate_text(text, max_chars) if text else ""

    def _workspace(self, workspace: Path) -> Path:
        canonical = _canonical_workspace(workspace)
        try:
            self.root.relative_to(canonical)
        except ValueError:
            return canonical
        raise SessionError("MEMORY_IO_ERROR", "memory storage root must be outside workspace")

    def _path(self, workspace: Path) -> Path:
        identity = str(workspace)
        if os.name == "nt":
            identity = os.path.normcase(identity)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.root / "memories" / f"{digest}.json"

    def _read(self, workspace: Path) -> dict[str, Any]:
        path = self._path(workspace)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema_version": 1, "workspace": str(workspace), "items": []}
        except (OSError, UnicodeDecodeError):
            raise SessionError("MEMORY_IO_ERROR", "workspace memory could not be read") from None
        try:
            document = json.loads(text, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
        if not _valid_document(document, workspace):
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
        return document

    def _write(self, workspace: Path, document: dict[str, Any]) -> None:
        try:
            _atomic_write_text(
                self._path(workspace),
                json.dumps(document, ensure_ascii=False, indent=2),
            )
        except (OSError, ValueError):
            raise SessionError("MEMORY_SAVE_FAILED", "memory could not be saved") from None


def _valid_document(document: Any, workspace: Path) -> bool:
    if type(document) is not dict or set(document) != {"schema_version", "workspace", "items"}:
        return False
    if document["schema_version"] != _SCHEMA_VERSION or type(document["schema_version"]) is not int:
        return False
    if document["workspace"] != str(workspace) or type(document["items"]) is not list:
        return False
    if len(document["items"]) > _MAX_ITEMS:
        return False
    try:
        items = tuple(_parse_item(item) for item in document["items"])
    except SessionError:
        return False
    return len({item.id for item in items}) == len(items)


def _parse_item(value: Any) -> MemoryItem:
    if type(value) is not dict or set(value) != {"id", "text", "created_at"}:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    item_id, text, timestamp = value["id"], value["text"], value["created_at"]
    if type(item_id) is not str or _ITEM_ID.fullmatch(item_id) is None:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    if type(text) is not str or not text or len(text) > _MAX_TEXT_CHARS:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    if type(timestamp) is not str or not timestamp.endswith("Z"):
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
    if not _is_utc(parsed):
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    return MemoryItem(item_id, text, parsed)


def _item_payload(item: MemoryItem) -> dict[str, str]:
    return {
        "id": item.id,
        "text": item.text,
        "created_at": item.created_at.isoformat().replace("+00:00", "Z"),
    }


def _is_utc(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _invalid(message: str) -> None:
    raise SessionError("MEMORY_INVALID", message)

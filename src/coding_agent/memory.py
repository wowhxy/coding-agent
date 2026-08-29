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


_SCHEMA_VERSION = 2
_ITEM_ID = re.compile(r"[0-9a-f]{8}")
_MAX_ITEMS = 100
_MAX_TEXT_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    text: str
    created_at: datetime
    kind: str
    source: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    """Classification of a proposed memory against existing workspace items."""

    status: str
    existing: MemoryItem | None


_KINDS = frozenset({"command", "constraint", "convention", "architecture", "fact"})
_SOURCES = frozenset({"user", "observed", "confirmed_candidate"})


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
        return tuple(_parse_item(item, _SCHEMA_VERSION) for item in document["items"])

    def add(
        self,
        workspace: Path,
        text: str,
        sensitive_values: tuple[str, ...],
        *,
        kind: str = "fact",
        source: str = "user",
    ) -> MemoryItem:
        canonical = self._workspace(workspace)
        normalized = _prepare_text(text, sensitive_values)
        _validate_kind_source(kind, source)
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
        item = MemoryItem(item_id, normalized, created_at, kind, source, created_at)
        document["items"].append(_item_payload(item))
        self._write(canonical, document)
        return item

    def match(self, workspace: Path, text: str, kind: str) -> MemoryMatch:
        """Classify a proposed item using deterministic, intentionally narrow rules."""

        normalized = _prepare_text(text, ())
        _validate_kind_source(kind, "user")
        items = self.list(workspace)
        identity = _normalized_text(normalized)
        for item in items:
            if _normalized_text(item.text) == identity:
                return MemoryMatch("duplicate", item)
        topic = _topic_key(normalized)
        if topic is not None:
            for item in items:
                if item.kind == kind and _topic_key(item.text) == topic:
                    return MemoryMatch("conflict", item)
        return MemoryMatch("new", None)

    def replace(
        self,
        workspace: Path,
        item_id: str,
        text: str,
        sensitive_values: tuple[str, ...],
        *,
        kind: str,
        source: str,
    ) -> MemoryItem:
        """Replace one confirmed conflict while preserving stable identity metadata."""

        canonical = self._workspace(workspace)
        normalized = _prepare_text(text, sensitive_values)
        _validate_kind_source(kind, source)
        document = self._read(canonical)
        try:
            updated_at = self._clock()
        except Exception:
            raise SessionError("MEMORY_SAVE_FAILED", "memory could not be saved") from None
        if not _is_utc(updated_at):
            raise SessionError("MEMORY_SAVE_FAILED", "memory metadata is invalid")
        for index, payload in enumerate(document["items"]):
            current = _parse_item(payload, _SCHEMA_VERSION)
            if current.id != item_id:
                continue
            if updated_at < current.created_at:
                raise SessionError(
                    "MEMORY_SAVE_FAILED", "memory metadata is invalid"
                )
            item = MemoryItem(
                current.id,
                normalized,
                current.created_at,
                kind,
                source,
                updated_at,
            )
            document["items"][index] = _item_payload(item)
            self._write(canonical, document)
            return item
        _invalid("memory id was not found")

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

    def render_for_context(self, workspace: Path, max_chars: int = 8_000) -> str:
        """Render optional context without letting a bad memory file block a run."""

        try:
            return self.render(workspace, max_chars)
        except SessionError as error:
            if error.error_code in {"MEMORY_CORRUPT", "MEMORY_IO_ERROR"}:
                return ""
            raise

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
            return {"schema_version": _SCHEMA_VERSION, "workspace": str(workspace), "items": []}
        except (OSError, UnicodeDecodeError):
            raise SessionError("MEMORY_IO_ERROR", "workspace memory could not be read") from None
        try:
            document = json.loads(text, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
        if not _valid_document(document, workspace):
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
        if document["schema_version"] == 1:
            document = {
                "schema_version": _SCHEMA_VERSION,
                "workspace": document["workspace"],
                "items": [
                    _item_payload(_parse_item(item, 1))
                    for item in document["items"]
                ],
            }
        return document

    def _write(self, workspace: Path, document: dict[str, Any]) -> None:
        document["schema_version"] = _SCHEMA_VERSION
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
    version = document["schema_version"]
    if type(version) is not int or version not in (1, _SCHEMA_VERSION):
        return False
    if document["workspace"] != str(workspace) or type(document["items"]) is not list:
        return False
    if len(document["items"]) > _MAX_ITEMS:
        return False
    try:
        items = tuple(_parse_item(item, version) for item in document["items"])
    except SessionError:
        return False
    return len({item.id for item in items}) == len(items)


def _parse_item(value: Any, version: int) -> MemoryItem:
    fields = (
        {"id", "text", "created_at"}
        if version == 1
        else {"id", "text", "kind", "source", "created_at", "updated_at"}
    )
    if type(value) is not dict or set(value) != fields:
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
    if version == 1:
        return MemoryItem(item_id, text, parsed, "fact", "user", parsed)
    kind, source = value["kind"], value["source"]
    try:
        _validate_kind_source(kind, source, corrupt=True)
        updated_at = _parse_memory_timestamp(value["updated_at"])
    except SessionError:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
    if updated_at < parsed:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    return MemoryItem(item_id, text, parsed, kind, source, updated_at)


def _item_payload(item: MemoryItem) -> dict[str, str]:
    return {
        "id": item.id,
        "text": item.text,
        "kind": item.kind,
        "source": item.source,
        "created_at": item.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": item.updated_at.isoformat().replace("+00:00", "Z"),
    }


def _parse_memory_timestamp(value: Any) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
    if not _is_utc(parsed):
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    return parsed


def _prepare_text(text: Any, sensitive_values: tuple[str, ...]) -> str:
    if type(text) is not str:
        _invalid("memory text is invalid")
    normalized = text.strip()
    for sensitive in sensitive_values:
        if type(sensitive) is str and sensitive:
            normalized = normalized.replace(sensitive, "[REDACTED]")
    if not normalized or len(normalized) > _MAX_TEXT_CHARS:
        _invalid("memory text must contain 1 to 2000 characters")
    return normalized


def _validate_kind_source(kind: Any, source: Any, *, corrupt: bool = False) -> None:
    if type(kind) is str and kind in _KINDS and type(source) is str and source in _SOURCES:
        return
    if corrupt:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    _invalid("memory kind or source is invalid")


def _normalized_text(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def _topic_key(text: str) -> str | None:
    parts = re.split(r"\s*(?::|=|\bis\b|\buses?\b)\s*", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    return _normalized_text(parts[0])


def _is_utc(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _invalid(message: str) -> None:
    raise SessionError("MEMORY_INVALID", message)

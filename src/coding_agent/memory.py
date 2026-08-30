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
from .memory_retrieval import ContextMemory
from .session import SessionError
from .session_store import _atomic_write_text, _canonical_session_root, _canonical_workspace


_SCHEMA_VERSION = 3
_ITEM_ID = re.compile(r"[0-9a-f]{8}")
_MEMORY_KEY = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MAX_ITEMS = 100
_MAX_TEXT_CHARS = 2_000
_MAX_KEY_CHARS = 80
_MAX_TOTAL_CHARS = 50_000
_CREDENTIAL_VALUE = re.compile(
    r"\b(?:api[_ -]?key|token|password|secret|credential)\s*[:=]\s*\S+|"
    r"\bauthorization\s*:\s*bearer\s+\S+|\bsk-[A-Za-z0-9]{12,}",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    kind: str
    key: str
    content: str
    source: str
    created_at: datetime
    updated_at: datetime

    @property
    def text(self) -> str:
        """Compatibility alias for callers that previously consumed free text."""

        return self.content


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    """Classification of a proposed memory against existing workspace items."""

    status: str
    existing: MemoryItem | None


_KINDS = frozenset({"command", "constraint", "convention", "architecture", "fact"})
_SOURCES = frozenset(
    {
        "user",
        "observed",
        "confirmed_candidate",
        "USER_EXPLICIT",
        "CONFIG_OBSERVED",
        "TOOL_VERIFIED",
        "MODEL_INFERRED",
    }
)


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
        source: str = "USER_EXPLICIT",
        key: str | None = None,
    ) -> MemoryItem:
        canonical = self._workspace(workspace)
        prepared = _prepare_text(text, sensitive_values)
        _validate_kind_source(kind, source)
        memory_key, content = _prepare_key_content(key, prepared, kind)
        document = self._read(canonical)
        match = _match_items(
            tuple(_parse_item(item, _SCHEMA_VERSION) for item in document["items"]),
            memory_key,
            content,
        )
        if match.status in {"exact_duplicate", "normalized_duplicate"}:
            raise SessionError("MEMORY_DUPLICATE", "workspace memory already exists")
        if match.status == "conflict":
            raise SessionError("MEMORY_CONFLICT", "workspace memory key conflicts")
        if len(document["items"]) >= _MAX_ITEMS:
            raise SessionError("MEMORY_LIMIT", "workspace memory limit reached")
        if _document_chars(document) + len(memory_key) + len(content) > _MAX_TOTAL_CHARS:
            raise SessionError("MEMORY_LIMIT", "workspace memory size limit reached")
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
        item = MemoryItem(
            item_id,
            kind,
            memory_key,
            content,
            source,
            created_at,
            created_at,
        )
        document["items"].append(_item_payload(item))
        self._write(canonical, document)
        return item

    def match(
        self,
        workspace: Path,
        text: str,
        kind: str,
        *,
        key: str | None = None,
    ) -> MemoryMatch:
        """Classify a proposed item using deterministic, intentionally narrow rules."""

        prepared = _prepare_text(text, ())
        _validate_kind_source(kind, "user")
        memory_key, content = _prepare_key_content(key, prepared, kind)
        return _match_items(self.list(workspace), memory_key, content)

    def replace(
        self,
        workspace: Path,
        item_id: str,
        text: str,
        sensitive_values: tuple[str, ...],
        *,
        kind: str,
        source: str,
        key: str | None = None,
    ) -> MemoryItem:
        """Replace one confirmed conflict while preserving stable identity metadata."""

        canonical = self._workspace(workspace)
        prepared = _prepare_text(text, sensitive_values)
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
            memory_key, content = _prepare_key_content(
                current.key if key is None else key, prepared, kind
            )
            other_items = tuple(
                _parse_item(other, _SCHEMA_VERSION)
                for other_index, other in enumerate(document["items"])
                if other_index != index
            )
            match = _match_items(other_items, memory_key, content)
            if match.status in {"exact_duplicate", "normalized_duplicate"}:
                raise SessionError("MEMORY_DUPLICATE", "workspace memory already exists")
            if match.status == "conflict":
                raise SessionError("MEMORY_CONFLICT", "workspace memory key conflicts")
            projected = (
                _document_chars(document)
                - len(current.key)
                - len(current.content)
                + len(memory_key)
                + len(content)
            )
            if projected > _MAX_TOTAL_CHARS:
                raise SessionError("MEMORY_LIMIT", "workspace memory size limit reached")
            item = MemoryItem(
                current.id,
                kind,
                memory_key,
                content,
                source,
                current.created_at,
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
        text = "\n".join(
            f"[{item.id}] ({item.kind}) {item.key} = {item.content}"
            for item in self.list(workspace)
        )
        return truncate_text(text, max_chars) if text else ""

    def render_for_context(self, workspace: Path, max_chars: int = 8_000) -> str:
        """Render optional context without letting a bad memory file block a run."""

        try:
            return self.render(workspace, max_chars)
        except SessionError as error:
            if error.error_code in {"MEMORY_CORRUPT", "MEMORY_IO_ERROR"}:
                return ""
            raise

    def context_items_for_context(
        self, workspace: Path
    ) -> tuple[ContextMemory, ...]:
        """Return safe structured projections, or empty on optional-data corruption."""

        try:
            return tuple(
                ContextMemory(item.id, item.kind, item.key, item.content)
                for item in self.list(workspace)
            )
        except SessionError as error:
            if error.error_code in {"MEMORY_CORRUPT", "MEMORY_IO_ERROR"}:
                return ()
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
        if document["schema_version"] in (1, 2):
            legacy_version = document["schema_version"]
            document = {
                "schema_version": _SCHEMA_VERSION,
                "workspace": document["workspace"],
                "items": [
                    _item_payload(_parse_item(item, legacy_version))
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
    if type(version) is not int or version not in (1, 2, _SCHEMA_VERSION):
        return False
    if document["workspace"] != str(workspace) or type(document["items"]) is not list:
        return False
    if len(document["items"]) > _MAX_ITEMS:
        return False
    try:
        items = tuple(_parse_item(item, version) for item in document["items"])
    except SessionError:
        return False
    if len({item.id for item in items}) != len(items):
        return False
    if sum(len(item.key) + len(item.content) for item in items) > _MAX_TOTAL_CHARS:
        return False
    if version == _SCHEMA_VERSION and len({item.key for item in items}) != len(items):
        return False
    return True


def _parse_item(value: Any, version: int) -> MemoryItem:
    fields = (
        {"id", "text", "created_at"}
        if version == 1
        else {"id", "text", "kind", "source", "created_at", "updated_at"}
        if version == 2
        else {"id", "kind", "key", "content", "source", "created_at", "updated_at"}
    )
    if type(value) is not dict or set(value) != fields:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    item_id, timestamp = value["id"], value["created_at"]
    if type(item_id) is not str or _ITEM_ID.fullmatch(item_id) is None:
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
        text = value["text"]
        if type(text) is not str or not text or len(text) > _MAX_TEXT_CHARS:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
        key, content = _legacy_key_content(text, "fact")
        return MemoryItem(item_id, "fact", key, content, "user", parsed, parsed)
    if version == 2:
        text = value["text"]
        if type(text) is not str or not text or len(text) > _MAX_TEXT_CHARS:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    kind, source = value["kind"], value["source"]
    try:
        _validate_kind_source(kind, source, corrupt=True)
        updated_at = _parse_memory_timestamp(value["updated_at"])
    except SessionError:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
    if updated_at < parsed:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    if version == 2:
        key, content = _legacy_key_content(text, kind)
    else:
        key, content = value["key"], value["content"]
        try:
            _validate_key(key, corrupt=True)
        except SessionError:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt") from None
        if type(content) is not str or not content or len(content) > _MAX_TEXT_CHARS:
            raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    return MemoryItem(item_id, kind, key, content, source, parsed, updated_at)


def _item_payload(item: MemoryItem) -> dict[str, str]:
    return {
        "id": item.id,
        "kind": item.kind,
        "key": item.key,
        "content": item.content,
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
    candidate_for_scan = normalized.replace("[REDACTED]", "")
    if _CREDENTIAL_VALUE.search(candidate_for_scan):
        _invalid("memory content appears to contain a credential")
    return normalized


def _prepare_key_content(
    key: str | None, prepared_text: str, kind: str
) -> tuple[str, str]:
    if key is None:
        return _legacy_key_content(prepared_text, kind)
    _validate_key(key)
    return key, prepared_text


def _legacy_key_content(text: str, kind: str) -> tuple[str, str]:
    parts = re.split(
        r"\s*(?::|=|\bis\b|\buses?\b)\s*",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if (
        len(parts) == 2
        and parts[0].strip()
        and parts[1].strip()
        and re.fullmatch(r"[A-Za-z0-9_. -]+", parts[0].strip())
    ):
        key = ".".join(
            re.findall(r"[a-z0-9]+", parts[0].casefold())
        )[:_MAX_KEY_CHARS]
        if key and _MEMORY_KEY.fullmatch(key):
            return key, parts[1].strip()
    digest = hashlib.sha256(_normalized_text(text).encode("utf-8")).hexdigest()[:8]
    return f"{kind}.note.{digest}", text


def _validate_key(key: Any, *, corrupt: bool = False) -> None:
    valid = (
        type(key) is str
        and 0 < len(key) <= _MAX_KEY_CHARS
        and _MEMORY_KEY.fullmatch(key) is not None
    )
    if valid:
        return
    if corrupt:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    _invalid("memory key is invalid")


def _match_items(
    items: tuple[MemoryItem, ...], key: str, content: str
) -> MemoryMatch:
    for item in items:
        if item.key != key:
            continue
        if item.content == content:
            return MemoryMatch("exact_duplicate", item)
        if _normalized_text(item.content) == _normalized_text(content):
            return MemoryMatch("normalized_duplicate", item)
        return MemoryMatch("conflict", item)
    return MemoryMatch("new", None)


def _document_chars(document: dict[str, Any]) -> int:
    total = 0
    for payload in document["items"]:
        item = _parse_item(payload, _SCHEMA_VERSION)
        total += len(item.key) + len(item.content)
    return total


def _validate_kind_source(kind: Any, source: Any, *, corrupt: bool = False) -> None:
    if type(kind) is str and kind in _KINDS and type(source) is str and source in _SOURCES:
        return
    if corrupt:
        raise SessionError("MEMORY_CORRUPT", "workspace memory is corrupt")
    _invalid("memory kind or source is invalid")


def _normalized_text(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def _is_utc(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _invalid(message: str) -> None:
    raise SessionError("MEMORY_INVALID", message)

"""Atomic JSON persistence and workspace indexing for interactive sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .session import SessionError, SessionRecord, deserialize_session, serialize_session


_INDEX_SCHEMA_VERSION = 2
_INDEX_FIELDS = {"schema_version", "workspace", "latest_session_id", "session_ids"}
_SESSION_ID = re.compile(r"[0-9a-f]{12}")
_SESSION_ID_ATTEMPTS = 100


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Small display projection for one workspace session."""

    session_id: str
    name: str | None
    updated_at: datetime
    is_latest: bool


def resolve_session_home(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the platform-specific session storage root without creating it."""

    values = os.environ if environ is None else environ
    override = values.get("CODING_AGENT_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        local_data = values.get("LOCALAPPDATA")
        if local_data:
            return Path(local_data) / "coding-agent"
        return Path.home() / "AppData" / "Local" / "coding-agent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "coding-agent"
    xdg_data = values.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "coding-agent"
    return Path.home() / ".local" / "share" / "coding-agent"


def utc_now() -> datetime:
    """Return the current aware UTC time."""

    return datetime.now(timezone.utc)


def generate_session_id() -> str:
    """Generate a 12-character lowercase hexadecimal session identifier."""

    return secrets.token_hex(6)


class JsonSessionStore:
    """Persist strict session documents and per-workspace latest indexes."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], datetime] = utc_now,
        id_generator: Callable[[], str] = generate_session_id,
    ) -> None:
        self.root = _canonical_session_root(root)
        self._clock = clock
        self._id_generator = id_generator
        self._lock = threading.RLock()

    def create_session(self, workspace: Path, provider: str, model: str) -> SessionRecord:
        with self._lock:
            return self._create_session(workspace, provider, model)

    def _create_session(self, workspace: Path, provider: str, model: str) -> SessionRecord:
        canonical = _canonical_workspace(workspace)
        self._validate_root(canonical)
        if type(provider) is not str or not provider.strip() or type(model) is not str or not model.strip():
            raise SessionError("SESSION_SAVE_FAILED", "session metadata is invalid")
        timestamp = self._clock()
        if not _is_aware_utc(timestamp):
            raise SessionError("SESSION_SAVE_FAILED", "session timestamp is invalid")
        for _ in range(_SESSION_ID_ATTEMPTS):
            try:
                session_id = self._id_generator()
            except Exception:
                raise SessionError("SESSION_SAVE_FAILED", "session id generation failed") from None
            if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
                raise SessionError("SESSION_SAVE_FAILED", "session id generation failed")
            try:
                collision = self._session_path(session_id).exists()
            except OSError:
                raise SessionError("SESSION_IO_ERROR", "session storage could not be inspected") from None
            if not collision:
                return SessionRecord(
                    session_id=session_id,
                    workspace=canonical,
                    provider=provider,
                    model=model,
                    created_at=timestamp,
                    updated_at=timestamp,
                    messages=(),
                )
        raise SessionError("SESSION_SAVE_FAILED", "session id generation failed")

    def load_latest(self, workspace: Path) -> SessionRecord | None:
        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            index = self._read_index(canonical, missing_ok=True)
            if index is None or index["latest_session_id"] is None:
                return None
            return self.load_session(index["latest_session_id"], canonical)

    def load_session(self, session_id: str, workspace: Path) -> SessionRecord:
        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
                raise SessionError("SESSION_NOT_FOUND", "session was not found")
            try:
                text = self._session_path(session_id).read_text(encoding="utf-8")
            except FileNotFoundError:
                raise SessionError("SESSION_NOT_FOUND", "session was not found") from None
            except UnicodeDecodeError:
                raise SessionError("SESSION_CORRUPT", "session document is not valid UTF-8") from None
            except OSError:
                raise SessionError("SESSION_IO_ERROR", "session could not be read") from None
            record = deserialize_session(text)
            if record.session_id != session_id:
                raise SessionError(
                    "SESSION_CORRUPT",
                    "session identifier does not match requested session",
                )
            if _workspace_identity(record.workspace) != _workspace_identity(canonical):
                raise SessionError(
                    "SESSION_WORKSPACE_MISMATCH", "session belongs to a different workspace"
                )
            return record

    def save(self, record: SessionRecord) -> SessionRecord:
        with self._lock:
            return self._save(record)

    def _save(self, record: SessionRecord) -> SessionRecord:
        if not isinstance(record, SessionRecord):
            raise SessionError("SESSION_SAVE_FAILED", "session could not be saved")
        canonical = _canonical_workspace(record.workspace)
        self._validate_root(canonical)
        timestamp = self._clock()
        persisted = replace(record, workspace=canonical, updated_at=timestamp)
        try:
            session_text = serialize_session(persisted)
        except SessionError:
            raise SessionError("SESSION_SAVE_FAILED", "session could not be saved") from None

        current = self._read_index(canonical, missing_ok=True)
        session_ids = [] if current is None else list(current["session_ids"])
        session_ids = [item for item in session_ids if item != persisted.session_id]
        session_ids.append(persisted.session_id)
        index_text = _serialize_index(canonical, persisted.session_id, session_ids)
        try:
            _atomic_write_text(self._session_path(persisted.session_id), session_text)
        except (OSError, ValueError):
            raise SessionError("SESSION_SAVE_FAILED", "session could not be saved") from None
        try:
            _atomic_write_text(self._index_path(canonical), index_text)
        except (OSError, ValueError):
            raise SessionError(
                "SESSION_SAVE_FAILED",
                "session was saved but workspace index was not updated",
            ) from None
        return persisted

    def rename_session(self, record: SessionRecord, name: str) -> SessionRecord:
        """Persist a trimmed display name for a session."""

        if type(name) is not str:
            raise SessionError("SESSION_NAME_INVALID", "session name is invalid")
        normalized = name.strip()
        if not normalized or len(normalized) > 80:
            raise SessionError(
                "SESSION_NAME_INVALID", "session name must contain 1 to 80 characters"
            )
        with self._lock:
            return self._save(replace(record, name=normalized))

    def delete_session(self, session_id: str, workspace: Path) -> SessionRecord | None:
        """Delete one persisted session and return the next latest record."""

        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            record = self.load_session(session_id, canonical)
            index = self._read_index(canonical, missing_ok=False)
            assert index is not None
            if session_id not in index["session_ids"]:
                raise SessionError("SESSION_NOT_FOUND", "session was not found")
            remaining = [item for item in index["session_ids"] if item != session_id]
            latest = remaining[-1] if remaining else None
            source = self._session_path(record.session_id)
            tombstone = source.with_name(f".{source.name}.deleted")
            try:
                os.replace(source, tombstone)
            except OSError:
                raise SessionError("SESSION_SAVE_FAILED", "session could not be deleted") from None
            try:
                _atomic_write_text(
                    self._index_path(canonical),
                    _serialize_index(canonical, latest, remaining),
                )
            except (OSError, ValueError):
                try:
                    os.replace(tombstone, source)
                except OSError:
                    pass
                raise SessionError(
                    "SESSION_SAVE_FAILED", "session could not be deleted"
                ) from None
            try:
                tombstone.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            return self.load_session(latest, canonical) if latest is not None else None

    def list_sessions(self, workspace: Path) -> tuple[SessionSummary, ...]:
        """List all sessions for one workspace, newest first."""

        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            index = self._read_index(canonical, missing_ok=True)
            if index is None:
                return ()
            latest = index["latest_session_id"]
            records = [self.load_session(item, canonical) for item in index["session_ids"]]
            records.sort(key=lambda item: item.updated_at, reverse=True)
            return tuple(
                SessionSummary(
                    session_id=record.session_id,
                    name=record.name,
                    updated_at=record.updated_at,
                    is_latest=record.session_id == latest,
                )
                for record in records
            )

    def search_sessions(
        self, workspace: Path, query: str
    ) -> tuple[SessionSummary, ...]:
        """Search names and persisted protocol text within one workspace."""

        if type(query) is not str or not query.strip():
            raise SessionError("SESSION_SEARCH_INVALID", "search query is required")
        needle = query.strip().casefold()
        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            summaries = self.list_sessions(canonical)
            matches: list[SessionSummary] = []
            for summary in summaries:
                record = self.load_session(summary.session_id, canonical)
                if needle in _session_search_text(record).casefold():
                    matches.append(summary)
            return tuple(matches)

    def load_recall_records(self, workspace: Path) -> tuple[SessionRecord, ...]:
        """Load same-workspace canonical sessions, skipping malformed individuals."""

        with self._lock:
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            index = self._read_index(canonical, missing_ok=True)
            if index is None:
                return ()
            records: list[SessionRecord] = []
            for session_id in index["session_ids"]:
                try:
                    records.append(self.load_session(session_id, canonical))
                except SessionError as error:
                    if error.error_code in {
                        "SESSION_NOT_FOUND",
                        "SESSION_CORRUPT",
                        "SESSION_VERSION_UNSUPPORTED",
                        "SESSION_WORKSPACE_MISMATCH",
                        "SESSION_IO_ERROR",
                    }:
                        continue
                    raise
            return tuple(records)

    def _validate_root(self, workspace: Path) -> None:
        self.root = _canonical_session_root(self.root)
        try:
            self.root.relative_to(workspace)
        except ValueError:
            return
        raise SessionError(
            "SESSION_IO_ERROR",
            "session storage root must be outside workspace",
        )

    def _session_path(self, session_id: str) -> Path:
        return self.root / "sessions" / f"{session_id}.json"

    def _index_path(self, workspace: Path) -> Path:
        digest = hashlib.sha256(_workspace_identity(workspace).encode("utf-8")).hexdigest()
        return self.root / "workspaces" / f"{digest}.json"

    def _read_index(self, workspace: Path, missing_ok: bool) -> dict[str, Any] | None:
        try:
            text = self._index_path(workspace).read_text(encoding="utf-8")
        except FileNotFoundError:
            if missing_ok:
                return None
            raise SessionError("SESSION_IO_ERROR", "workspace index could not be read") from None
        except UnicodeDecodeError:
            raise SessionError("SESSION_INDEX_CORRUPT", "workspace index is corrupt") from None
        except OSError:
            raise SessionError("SESSION_IO_ERROR", "workspace index could not be read") from None
        try:
            document = json.loads(text, parse_constant=_reject_json_constant)
        except ValueError:
            raise SessionError("SESSION_INDEX_CORRUPT", "workspace index is corrupt") from None
        if not _valid_index(document, workspace):
            raise SessionError("SESSION_INDEX_CORRUPT", "workspace index is corrupt")
        return document


def _canonical_workspace(workspace: Path) -> Path:
    try:
        candidate = Path(workspace).resolve(strict=True)
        if not candidate.is_dir():
            raise NotADirectoryError
    except (OSError, RuntimeError, TypeError):
        raise SessionError("SESSION_IO_ERROR", "workspace directory is unavailable") from None
    return candidate


def _canonical_session_root(root: Path) -> Path:
    try:
        return Path(root).resolve(strict=False)
    except (OSError, RuntimeError, TypeError):
        raise SessionError("SESSION_IO_ERROR", "session storage root is unavailable") from None


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constants are not supported")


def _workspace_identity(workspace: Path) -> str:
    value = str(workspace)
    if os.name == "nt":
        value = os.path.normcase(value)
    return value


def _valid_index(document: Any, workspace: Path) -> bool:
    if type(document) is not dict or set(document) != _INDEX_FIELDS:
        return False
    version = document["schema_version"]
    if type(version) is not int or version not in (1, _INDEX_SCHEMA_VERSION):
        return False
    stored_workspace = document["workspace"]
    if type(stored_workspace) is not str or not Path(stored_workspace).is_absolute():
        return False
    if _workspace_identity(Path(stored_workspace)) != _workspace_identity(workspace):
        return False
    latest = document["latest_session_id"]
    identifiers = document["session_ids"]
    if type(identifiers) is not list:
        return False
    if any(type(item) is not str or _SESSION_ID.fullmatch(item) is None for item in identifiers):
        return False
    if version == 1 and not identifiers:
        return False
    if not identifiers:
        return version == 2 and latest is None
    if type(latest) is not str or _SESSION_ID.fullmatch(latest) is None:
        return False
    if len(set(identifiers)) != len(identifiers) or latest not in identifiers:
        return False
    return True


def _serialize_index(
    workspace: Path, latest_session_id: str | None, session_ids: list[str]
) -> str:
    return json.dumps(
        {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "workspace": str(workspace),
            "latest_session_id": latest_session_id,
            "session_ids": session_ids,
        },
        ensure_ascii=False,
        indent=2,
    )


def _session_search_text(record: SessionRecord) -> str:
    values = [record.name or ""]
    for message in record.messages:
        values.extend((message.content or "", message.tool_call_id or ""))
        for call in message.tool_calls:
            values.extend((call.id, call.name, call.arguments_json))
    return "\n".join(values)


def _is_aware_utc(value: Any) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace one UTF-8 file and clean only this call's temp file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            if os.name != "nt":
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

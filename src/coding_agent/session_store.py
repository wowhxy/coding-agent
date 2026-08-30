"""Atomic JSON persistence and workspace indexing for interactive sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .session import (
    SessionError,
    SessionNameSource,
    SessionRecord,
    deserialize_session,
    serialize_session,
)
from .session_index import FtsUnavailableError, SessionIndex
from .session_search import (
    SearchLocator,
    SessionSearchResult,
    bounded_snippet,
    matches_document,
    materialize_document,
    searchable_documents,
)


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


@dataclass(slots=True)
class SessionStoreReport:
    """Content-free counters for the most recent store operation."""

    latest_fast_path_used: bool = False
    session_files_loaded: int = 0
    full_history_files_loaded: int = 0
    catalog_entries_loaded: int = 0
    search_backend: str = "none"
    search_hits: int = 0
    index_rebuilt: bool = False


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
        self.last_report = SessionStoreReport()

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
            self._reset_report()
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            pointer = self._read_latest_pointer(canonical)
            if pointer is not None:
                try:
                    record = self._load_session(pointer, canonical)
                except SessionError as error:
                    if error.error_code not in {
                        "SESSION_NOT_FOUND",
                        "SESSION_CORRUPT",
                        "SESSION_VERSION_UNSUPPORTED",
                        "SESSION_WORKSPACE_MISMATCH",
                        "SESSION_IO_ERROR",
                    }:
                        raise
                else:
                    self.last_report.latest_fast_path_used = True
                    return record

            index = self._ensure_index(canonical)
            for latest in index.latest_candidates():
                try:
                    record = self._load_session(latest, canonical)
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
                index.set_latest(latest)
                self._write_latest_pointer(canonical, latest)
                return record
            index.set_latest(None)
            self._clear_latest_pointer(canonical)
            return None

    def load_session(self, session_id: str, workspace: Path) -> SessionRecord:
        with self._lock:
            self._reset_report()
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            return self._load_session(session_id, canonical)

    def _load_session(self, session_id: str, workspace: Path) -> SessionRecord:
        if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
            raise SessionError("SESSION_NOT_FOUND", "session was not found")
        self.last_report.session_files_loaded += 1
        self.last_report.full_history_files_loaded += 1
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
        if _workspace_identity(record.workspace) != _workspace_identity(workspace):
            raise SessionError(
                "SESSION_WORKSPACE_MISMATCH", "session belongs to a different workspace"
            )
        return record

    def save(self, record: SessionRecord) -> SessionRecord:
        with self._lock:
            self._reset_report()
            return self._save(record)

    def _save(
        self, record: SessionRecord, *, make_latest: bool = True
    ) -> SessionRecord:
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

        try:
            _atomic_write_text(self._session_path(persisted.session_id), session_text)
        except (OSError, ValueError):
            raise SessionError("SESSION_SAVE_FAILED", "session could not be saved") from None

        index = SessionIndex(self.root, canonical)
        try:
            self._ensure_index(canonical, index=index)
            index.upsert(persisted)
            previous_latest = index.latest()
            latest = (
                persisted.session_id
                if make_latest or previous_latest is None
                else previous_latest
            )
            index.set_latest(latest)
        except (OSError, sqlite3.Error, ValueError):
            try:
                index.mark_stale()
            except (OSError, ValueError):
                pass
            latest = persisted.session_id if make_latest else self._read_latest_pointer(canonical)
        if latest is not None:
            try:
                self._write_latest_pointer(canonical, latest)
            except (OSError, ValueError):
                try:
                    index.mark_stale()
                except (OSError, ValueError):
                    pass
        return persisted

    def rename_session(
        self,
        record: SessionRecord,
        name: str,
        *,
        make_latest: bool = True,
    ) -> SessionRecord:
        """Persist a trimmed display name for a session."""

        if type(name) is not str:
            raise SessionError("SESSION_NAME_INVALID", "session name is invalid")
        normalized = name.strip()
        if not normalized or len(normalized) > 80:
            raise SessionError(
                "SESSION_NAME_INVALID", "session name must contain 1 to 80 characters"
            )
        with self._lock:
            return self._save(
                replace(
                    record,
                    name=normalized,
                    name_source=SessionNameSource.MANUAL,
                ),
                make_latest=make_latest,
            )

    def delete_session(self, session_id: str, workspace: Path) -> SessionRecord | None:
        """Delete one persisted session and return the next latest record."""

        with self._lock:
            self._reset_report()
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            record = self._load_session(session_id, canonical)
            source = self._session_path(record.session_id)
            tombstone = source.with_name(f".{source.name}.deleted")
            try:
                os.replace(source, tombstone)
            except OSError:
                raise SessionError("SESSION_SAVE_FAILED", "session could not be deleted") from None
            index = SessionIndex(self.root, canonical)
            latest: str | None = None
            try:
                self._ensure_index(canonical, index=index)
                index.remove(session_id)
                latest = index.latest()
            except (OSError, sqlite3.Error, ValueError):
                try:
                    index.mark_stale()
                except (OSError, ValueError):
                    pass
                records = tuple(self._records_for_rebuild(canonical))
                latest = (
                    max(
                        records, key=lambda item: (item.updated_at, item.session_id)
                    ).session_id
                    if records
                    else None
                )
            try:
                tombstone.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            try:
                if latest is None:
                    self._clear_latest_pointer(canonical)
                else:
                    self._write_latest_pointer(canonical, latest)
            except (OSError, ValueError):
                try:
                    index.mark_stale()
                except (OSError, ValueError):
                    pass
            return self._load_session(latest, canonical) if latest is not None else None

    def list_sessions(
        self,
        workspace: Path,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[SessionSummary, ...]:
        """List a lightweight metadata page for one workspace, newest first."""

        if limit is not None and (type(limit) is not int or limit <= 0):
            raise SessionError("SESSION_LIST_INVALID", "session list limit is invalid")
        if type(offset) is not int or offset < 0:
            raise SessionError("SESSION_LIST_INVALID", "session list offset is invalid")
        with self._lock:
            self._reset_report()
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            index = self._ensure_index(canonical)
            latest = self._read_latest_pointer(canonical)
            if latest is None or not index.contains(latest):
                latest = index.latest()
                if latest is not None:
                    try:
                        self._write_latest_pointer(canonical, latest)
                    except (OSError, ValueError):
                        pass
            entries = index.list(limit=limit, offset=offset)
            self.last_report.catalog_entries_loaded = len(entries)
            return tuple(
                SessionSummary(
                    session_id=entry.session_id,
                    name=entry.name,
                    updated_at=entry.updated_at,
                    is_latest=entry.session_id == latest,
                )
                for entry in entries
            )

    def search_sessions(
        self, workspace: Path, query: str, *, limit: int = 20
    ) -> tuple[SessionSummary, ...]:
        """Return one lightweight catalog result per matching session."""

        results = self.search_session_results(workspace, query, limit=limit)
        latest = self._read_latest_pointer(_canonical_workspace(workspace))
        seen: set[str] = set()
        summaries: list[SessionSummary] = []
        for result in results:
            if result.session_id in seen:
                continue
            seen.add(result.session_id)
            summaries.append(
                SessionSummary(
                    result.session_id,
                    result.name,
                    result.updated_at,
                    result.session_id == latest,
                )
            )
        return tuple(summaries)

    def search_session_results(
        self,
        workspace: Path,
        query: str,
        *,
        limit: int = 20,
        exclude_session_id: str | None = None,
        fts_enabled: bool = True,
    ) -> tuple[SessionSearchResult, ...]:
        """Locate first, then materialize only bounded canonical excerpts."""

        if type(query) is not str or not query.strip():
            raise SessionError("SESSION_SEARCH_INVALID", "search query is required")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise SessionError("SESSION_SEARCH_INVALID", "search result limit is invalid")
        with self._lock:
            self._reset_report()
            canonical = _canonical_workspace(workspace)
            self._validate_root(canonical)
            index = self._ensure_index(canonical)
            if fts_enabled:
                try:
                    locators = index.search(
                        query,
                        limit=limit,
                        exclude_session_id=exclude_session_id,
                    )
                except FtsUnavailableError:
                    locators = ()
                    fts_enabled = False
                except (OSError, sqlite3.Error, ValueError):
                    try:
                        index.mark_stale()
                        index = self._ensure_index(canonical, index=index)
                        locators = index.search(
                            query,
                            limit=limit,
                            exclude_session_id=exclude_session_id,
                        )
                    except (OSError, sqlite3.Error, ValueError, FtsUnavailableError):
                        locators = ()
                        fts_enabled = False
            else:
                locators = ()

            results = (
                self._materialize_locators(canonical, locators, limit)
                if fts_enabled
                else self._scan_search(
                    canonical,
                    index,
                    query,
                    limit=limit,
                    exclude_session_id=exclude_session_id,
                )
            )
            self.last_report.search_backend = "fts5" if fts_enabled else "scan"
            self.last_report.search_hits = len(results)
            return results

    def _materialize_locators(
        self,
        workspace: Path,
        locators: tuple[SearchLocator, ...],
        limit: int,
    ) -> tuple[SessionSearchResult, ...]:
        records: dict[str, SessionRecord | None] = {}
        results: list[SessionSearchResult] = []
        for locator in locators:
            if locator.session_id not in records:
                try:
                    records[locator.session_id] = self._load_session(
                        locator.session_id, workspace
                    )
                except SessionError:
                    records[locator.session_id] = None
            record = records[locator.session_id]
            if record is None:
                continue
            text = materialize_document(record, locator.ordinal, locator.source)
            if not text:
                continue
            results.append(
                SessionSearchResult(
                    record.session_id,
                    record.name,
                    record.updated_at,
                    bounded_snippet(text),
                    -locator.rank,
                    locator.ordinal,
                    locator.source,
                )
            )
            if len(results) == limit:
                break
        return tuple(results)

    def _scan_search(
        self,
        workspace: Path,
        index: SessionIndex,
        query: str,
        *,
        limit: int,
        exclude_session_id: str | None,
    ) -> tuple[SessionSearchResult, ...]:
        ranked: list[SessionSearchResult] = []
        for entry in index.list(limit=None, offset=0):
            if entry.session_id == exclude_session_id:
                continue
            try:
                record = self._load_session(entry.session_id, workspace)
            except SessionError:
                continue
            for document in searchable_documents(record):
                matched, score = matches_document(document, query)
                if matched:
                    ranked.append(
                        SessionSearchResult(
                            record.session_id,
                            record.name,
                            record.updated_at,
                            bounded_snippet(document.text),
                            score,
                            document.ordinal,
                            document.source,
                        )
                    )
        ranked.sort(
            key=lambda item: (
                -item.score,
                -item.updated_at.timestamp(),
                item.session_id,
                item.ordinal,
            )
        )
        return tuple(ranked[:limit])

    def latest_pointer_path(self, workspace: Path) -> Path:
        """Return the workspace-scoped tiny latest pointer path."""

        canonical = _canonical_workspace(workspace)
        self._validate_root(canonical)
        return SessionIndex(self.root, canonical).latest_path

    def _reset_report(self) -> None:
        self.last_report = SessionStoreReport()

    def _ensure_index(
        self, workspace: Path, *, index: SessionIndex | None = None
    ) -> SessionIndex:
        selected = index or SessionIndex(self.root, workspace)
        rebuilt = selected.ensure(
            self._records_for_rebuild(workspace),
            latest_hint=lambda: self._legacy_latest_hint(workspace),
        )
        self.last_report.index_rebuilt = self.last_report.index_rebuilt or rebuilt
        return selected

    def _records_for_rebuild(self, workspace: Path):
        sessions = self.root / "sessions"
        try:
            paths = sorted(sessions.glob("*.json"))
        except OSError:
            return
        for path in paths:
            self.last_report.session_files_loaded += 1
            self.last_report.full_history_files_loaded += 1
            try:
                record = deserialize_session(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SessionError):
                continue
            if _workspace_identity(record.workspace) == _workspace_identity(workspace):
                yield record

    def _legacy_latest_hint(self, workspace: Path) -> str | None:
        try:
            index = self._read_index(workspace, missing_ok=True)
        except SessionError:
            return None
        return None if index is None else index["latest_session_id"]

    def _read_latest_pointer(self, workspace: Path) -> str | None:
        path = SessionIndex(self.root, workspace).latest_path
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError, OSError):
            return None
        if text.endswith("\n"):
            text = text[:-1]
        if _SESSION_ID.fullmatch(text) is None:
            return None
        return text

    def _write_latest_pointer(self, workspace: Path, session_id: str) -> None:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise ValueError("invalid session id")
        _atomic_write_text(SessionIndex(self.root, workspace).latest_path, session_id + "\n")

    def _clear_latest_pointer(self, workspace: Path) -> None:
        try:
            SessionIndex(self.root, workspace).latest_path.unlink(missing_ok=True)
        except OSError:
            pass

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

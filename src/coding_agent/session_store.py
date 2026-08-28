"""Atomic JSON persistence and workspace indexing for interactive sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .session import SessionError, SessionRecord, deserialize_session, serialize_session


_INDEX_SCHEMA_VERSION = 1
_INDEX_FIELDS = {"schema_version", "workspace", "latest_session_id", "session_ids"}
_SESSION_ID = re.compile(r"[0-9a-f]{12}")
_SESSION_ID_ATTEMPTS = 100


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

    def create_session(self, workspace: Path, provider: str, model: str) -> SessionRecord:
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
        canonical = _canonical_workspace(workspace)
        self._validate_root(canonical)
        index = self._read_index(canonical, missing_ok=True)
        if index is None:
            return None
        return self.load_session(index["latest_session_id"], canonical)

    def load_session(self, session_id: str, workspace: Path) -> SessionRecord:
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
        index_text = json.dumps(
            {
                "schema_version": _INDEX_SCHEMA_VERSION,
                "workspace": str(canonical),
                "latest_session_id": persisted.session_id,
                "session_ids": session_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
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
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        return False
    stored_workspace = document["workspace"]
    if type(stored_workspace) is not str or not Path(stored_workspace).is_absolute():
        return False
    if _workspace_identity(Path(stored_workspace)) != _workspace_identity(workspace):
        return False
    latest = document["latest_session_id"]
    identifiers = document["session_ids"]
    if type(latest) is not str or _SESSION_ID.fullmatch(latest) is None:
        return False
    if type(identifiers) is not list or not identifiers:
        return False
    if any(type(item) is not str or _SESSION_ID.fullmatch(item) is None for item in identifiers):
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

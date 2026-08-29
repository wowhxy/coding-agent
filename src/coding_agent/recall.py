"""Temporary same-workspace recall over canonical persisted sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .context import truncate_text
from .protocol import Role
from .session import SessionError, SessionRecord
from .session_store import JsonSessionStore


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_PAST_REFERENCE = re.compile(
    r"上次|之前|昨天|以前|\bprevious\b|\bearlier\b|\blast\s+time\b",
    re.IGNORECASE,
)
_MAX_INDEX_CONTENT = 2_000
_MAX_EXCERPT = 500
_MAX_RESULTS = 20


@dataclass(frozen=True, slots=True)
class RecallEntry:
    session_id: str
    source: str
    excerpt: str
    ordinal: int
    timestamp: datetime
    score: int


@dataclass(frozen=True, slots=True)
class _RecallDocument:
    session_id: str
    source: str
    content: str
    ordinal: int
    timestamp: datetime


class RecallService:
    """Search disposable FTS5 data with a deterministic canonical scan fallback."""

    def __init__(
        self,
        store: JsonSessionStore,
        *,
        fts_enabled: bool = True,
    ) -> None:
        self.store = store
        self.fts_enabled = fts_enabled
        self.backend = "unknown"

    def index_path(self, workspace: Path) -> Path:
        identity = str(Path(workspace).resolve(strict=True))
        if os.name == "nt":
            identity = os.path.normcase(identity)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.store.root / "recall" / f"{digest}.sqlite3"

    def search(
        self,
        workspace: Path,
        query: str,
        *,
        exclude_session_id: str | None = None,
        limit: int = 5,
    ) -> tuple[RecallEntry, ...]:
        if type(query) is not str or not query.strip():
            raise SessionError("RECALL_INVALID", "recall query is required")
        if type(limit) is not int or not 1 <= limit <= _MAX_RESULTS:
            raise SessionError("RECALL_INVALID", "recall result limit is invalid")
        try:
            records = self.store.load_recall_records(workspace)
        except SessionError:
            self.backend = "scan"
            return ()
        documents = _documents(records)
        candidates = documents
        if self.fts_enabled:
            try:
                candidates = self._fts_candidates(
                    workspace, records, documents, query
                )
                self.backend = "fts5"
            except (OSError, sqlite3.Error, ValueError):
                self.backend = "scan"
                candidates = documents
        else:
            self.backend = "scan"
        return _rank(
            candidates,
            query,
            exclude_session_id=exclude_session_id,
            limit=limit,
        )

    def _fts_candidates(
        self,
        workspace: Path,
        records: tuple[SessionRecord, ...],
        documents: tuple[_RecallDocument, ...],
        query: str,
    ) -> tuple[_RecallDocument, ...]:
        path = self.index_path(workspace)
        fingerprint = _fingerprint(documents)
        try:
            self._ensure_index(path, fingerprint, documents)
            return _query_index(path, query)
        except (OSError, sqlite3.Error, ValueError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._build_index(path, fingerprint, documents)
            return _query_index(path, query)

    def _ensure_index(
        self,
        path: Path,
        fingerprint: str,
        documents: tuple[_RecallDocument, ...],
    ) -> None:
        if not path.exists():
            self._build_index(path, fingerprint, documents)
            return
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT value FROM recall_metadata WHERE key = 'fingerprint'"
            ).fetchone()
            connection.execute("SELECT count(*) FROM recall_documents").fetchone()
        if row is None or row[0] != fingerprint:
            path.unlink(missing_ok=True)
            self._build_index(path, fingerprint, documents)

    def _build_index(
        self,
        path: Path,
        fingerprint: str,
        documents: tuple[_RecallDocument, ...],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE recall_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE recall_documents USING fts5("
                "session_id UNINDEXED, source UNINDEXED, ordinal UNINDEXED, "
                "timestamp UNINDEXED, content)"
            )
            connection.executemany(
                "INSERT INTO recall_documents "
                "(session_id, source, ordinal, timestamp, content) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        item.session_id,
                        item.source,
                        item.ordinal,
                        item.timestamp.isoformat(),
                        item.content,
                    )
                    for item in documents
                ),
            )
            connection.execute(
                "INSERT INTO recall_metadata (key, value) VALUES ('fingerprint', ?)",
                (fingerprint,),
            )


def should_automatic_recall(text: str) -> bool:
    return type(text) is str and _PAST_REFERENCE.search(text) is not None


def _documents(records: tuple[SessionRecord, ...]) -> tuple[_RecallDocument, ...]:
    documents: list[_RecallDocument] = []
    for record in records:
        metadata = " ".join(
            value for value in (record.name, record.provider, record.model) if value
        )
        if metadata:
            documents.append(
                _RecallDocument(
                    record.session_id,
                    "metadata",
                    truncate_text(metadata, _MAX_INDEX_CONTENT),
                    -1,
                    record.updated_at,
                )
            )
        for ordinal, message in enumerate(record.messages):
            if message.role in {Role.USER, Role.ASSISTANT} and message.content:
                documents.append(
                    _RecallDocument(
                        record.session_id,
                        message.role.value,
                        truncate_text(message.content, _MAX_INDEX_CONTENT),
                        ordinal,
                        record.updated_at,
                    )
                )
            elif message.role is Role.TOOL:
                content = _useful_tool_content(message.content or "")
                if content:
                    documents.append(
                        _RecallDocument(
                            record.session_id,
                            "tool",
                            truncate_text(content, _MAX_INDEX_CONTENT),
                            ordinal,
                            record.updated_at,
                        )
                    )
    return tuple(documents)


def _useful_tool_content(content: str) -> str:
    try:
        payload = json.loads(content)
    except ValueError:
        return content
    if not isinstance(payload, dict):
        return content
    values = [
        value
        for key in ("output", "error_message")
        if isinstance((value := payload.get(key)), str) and value
    ]
    return "\n".join(values)


def _fingerprint(documents: tuple[_RecallDocument, ...]) -> str:
    payload = json.dumps(
        [
            (
                item.session_id,
                item.source,
                item.content,
                item.ordinal,
                item.timestamp.isoformat(),
            )
            for item in documents
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _query_index(path: Path, query: str) -> tuple[_RecallDocument, ...]:
    tokens = sorted(_tokens(query))
    if not tokens:
        return ()
    expression = " OR ".join(
        '"' + token.replace('"', '""') + '"' for token in tokens
    )
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT session_id, source, ordinal, timestamp, content "
            "FROM recall_documents WHERE recall_documents MATCH ?",
            (expression,),
        ).fetchall()
    return tuple(
        _RecallDocument(
            session_id=row[0],
            source=row[1],
            ordinal=int(row[2]),
            timestamp=datetime.fromisoformat(row[3]),
            content=row[4],
        )
        for row in rows
    )


def _rank(
    documents: tuple[_RecallDocument, ...],
    query: str,
    *,
    exclude_session_id: str | None,
    limit: int,
) -> tuple[RecallEntry, ...]:
    query_tokens = _tokens(query)
    phrase = query.strip().casefold()
    ranked: list[RecallEntry] = []
    source_bonus = {"tool": 3, "user": 2, "assistant": 1, "metadata": 0}
    for item in documents:
        if item.session_id == exclude_session_id:
            continue
        content_folded = item.content.casefold()
        overlap = len(query_tokens & _tokens(item.content))
        phrase_match = int(bool(phrase) and phrase in content_folded)
        if overlap == 0 and phrase_match == 0:
            continue
        score = overlap * 10 + phrase_match * 20 + source_bonus.get(item.source, 0)
        ranked.append(
            RecallEntry(
                item.session_id,
                item.source,
                truncate_text(item.content, _MAX_EXCERPT),
                item.ordinal,
                item.timestamp,
                score,
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.score,
            -item.timestamp.timestamp(),
            item.session_id,
            item.ordinal,
        )
    )
    return tuple(ranked[:limit])


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text)}

"""Disposable per-workspace metadata index for canonical JSON sessions."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Iterable, Iterator

from .session import SessionRecord
from .session_search import SearchLocator, searchable_documents, search_terms


SESSION_INDEX_SCHEMA_VERSION = 2


class FtsUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    session_id: str
    name: str | None
    created_at: datetime
    updated_at: datetime
    provider: str
    model: str
    preview: str


class SessionIndex:
    """Keep lightweight metadata derived from one workspace's Session JSON."""

    def __init__(self, root: Path, workspace: Path) -> None:
        self.root = Path(root)
        self.workspace = Path(workspace)
        digest = hashlib.sha256(_workspace_identity(workspace).encode("utf-8")).hexdigest()
        self.directory = self.root / "workspaces" / digest
        self.database_path = self.directory / "session_index.sqlite3"
        self.latest_path = self.directory / "latest"
        self.stale_path = self.directory / "stale"

    def ensure(
        self,
        records: Iterable[SessionRecord],
        *,
        latest_hint: str | None | Callable[[], str | None] = None,
    ) -> bool:
        """Return whether missing, stale, corrupt, or incompatible state was rebuilt."""

        if self.stale_path.exists() or not self.database_path.exists():
            self.rebuild(records, latest_hint=_resolve_hint(latest_hint))
            return True
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                workspace = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'workspace'"
                ).fetchone()
                connection.execute("SELECT session_id FROM catalog LIMIT 1").fetchone()
            if (
                row is None
                or row[0] != str(SESSION_INDEX_SCHEMA_VERSION)
                or workspace is None
                or workspace[0] != _workspace_identity(self.workspace)
            ):
                raise sqlite3.DatabaseError("incompatible session index")
        except (OSError, sqlite3.Error, ValueError):
            self.rebuild(records, latest_hint=_resolve_hint(latest_hint))
            return True
        return False

    def rebuild(
        self,
        records: Iterable[SessionRecord],
        *,
        latest_hint: str | None = None,
    ) -> None:
        """Atomically replace derived state without modifying canonical records."""

        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                dir=self.directory,
                prefix=".session_index.",
                suffix=".sqlite3",
            )
            os.close(descriptor)
            temporary = Path(name)
            materialized = tuple(records)
            with _sqlite_connection(temporary) as connection:
                _create_schema(connection, self.workspace)
                connection.executemany(
                    "INSERT INTO catalog "
                    "(session_id, name, created_at, updated_at, provider, model, preview) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (_catalog_values(record) for record in materialized),
                )
                if _fts_available(connection):
                    for record in materialized:
                        _replace_search_documents(connection, record)
                identifiers = {record.session_id for record in materialized}
                latest = latest_hint if latest_hint in identifiers else _newest_id(materialized)
                connection.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES ('latest_session_id', ?)",
                    (latest or "",),
                )
            os.replace(temporary, self.database_path)
            temporary = None
            self.stale_path.unlink(missing_ok=True)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def upsert(self, record: SessionRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO catalog "
                "(session_id, name, created_at, updated_at, provider, model, preview) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "name=excluded.name, created_at=excluded.created_at, "
                "updated_at=excluded.updated_at, provider=excluded.provider, "
                "model=excluded.model, preview=excluded.preview",
                _catalog_values(record),
            )
            if _fts_available(connection):
                _replace_search_documents(connection, record)

    def remove(self, session_id: str) -> None:
        with self._connection() as connection:
            if _fts_available(connection):
                _remove_search_documents(connection, session_id)
            connection.execute("DELETE FROM catalog WHERE session_id = ?", (session_id,))
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'latest_session_id'"
            ).fetchone()
            if row is not None and row[0] == session_id:
                replacement = connection.execute(
                    "SELECT session_id FROM catalog "
                    "ORDER BY updated_at DESC, session_id ASC LIMIT 1"
                ).fetchone()
                connection.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                    "('latest_session_id', ?)",
                    (replacement[0] if replacement is not None else "",),
                )

    def set_latest(self, session_id: str | None) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                "('latest_session_id', ?)",
                (session_id or "",),
            )

    def latest(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'latest_session_id'"
            ).fetchone()
            if row is not None and row[0]:
                exists = connection.execute(
                    "SELECT 1 FROM catalog WHERE session_id = ?", (row[0],)
                ).fetchone()
                if exists is not None:
                    return str(row[0])
            row = connection.execute(
                "SELECT session_id FROM catalog "
                "ORDER BY updated_at DESC, session_id ASC LIMIT 1"
            ).fetchone()
            return None if row is None else str(row[0])

    def contains(self, session_id: str) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM catalog WHERE session_id = ?", (session_id,)
            ).fetchone() is not None

    def latest_candidates(self) -> tuple[str, ...]:
        preferred = self.latest()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT session_id FROM catalog "
                "ORDER BY updated_at DESC, session_id ASC"
            ).fetchall()
        values = ([preferred] if preferred is not None else []) + [
            str(row[0]) for row in rows
        ]
        return tuple(dict.fromkeys(values))

    def list(self, *, limit: int | None, offset: int) -> tuple[CatalogEntry, ...]:
        sql = (
            "SELECT session_id, name, created_at, updated_at, provider, model, preview "
            "FROM catalog ORDER BY updated_at DESC, session_id ASC"
        )
        parameters: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters = (limit, offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            parameters = (offset,)
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return tuple(_entry(row) for row in rows)

    def search(
        self,
        query: str,
        *,
        limit: int,
        exclude_session_id: str | None = None,
    ) -> tuple[SearchLocator, ...]:
        terms = search_terms(query)
        if not terms:
            return ()
        expression = " OR ".join(
            '"' + term.replace('"', '""') + '"' for term in terms
        )
        sql = (
            "SELECT locators.session_id, locators.ordinal, locators.source, "
            "catalog.updated_at, bm25(search_fts) AS rank "
            "FROM search_fts "
            "JOIN search_locators AS locators ON locators.doc_id = search_fts.rowid "
            "JOIN catalog ON catalog.session_id = locators.session_id "
            "WHERE search_fts MATCH ?"
        )
        parameters: list[object] = [expression]
        if exclude_session_id is not None:
            sql += " AND locators.session_id <> ?"
            parameters.append(exclude_session_id)
        sql += (
            " ORDER BY rank ASC, catalog.updated_at DESC, "
            "locators.session_id ASC, locators.ordinal ASC LIMIT ?"
        )
        parameters.append(limit)
        with self._connection() as connection:
            if not _fts_available(connection):
                raise FtsUnavailableError("SQLite FTS5 is unavailable")
            rows = connection.execute(sql, parameters).fetchall()
        return tuple(
            SearchLocator(
                session_id=str(row[0]),
                ordinal=int(row[1]),
                source=str(row[2]),
                updated_at=datetime.fromisoformat(str(row[3])),
                rank=float(row[4]),
            )
            for row in rows
        )

    def mark_stale(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.stale_path.with_suffix(".tmp")
        try:
            temporary.write_text("rebuild required\n", encoding="utf-8")
            os.replace(temporary, self.stale_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with _sqlite_connection(self.database_path) as connection:
            yield connection


@contextmanager
def _sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _create_schema(connection: sqlite3.Connection, workspace: Path) -> None:
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE catalog ("
        "session_id TEXT PRIMARY KEY, name TEXT, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "preview TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX catalog_recency ON catalog(updated_at DESC, session_id ASC)"
    )
    connection.execute(
        "CREATE TABLE search_locators ("
        "doc_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "ordinal INTEGER NOT NULL, source TEXT NOT NULL, "
        "FOREIGN KEY(session_id) REFERENCES catalog(session_id) ON DELETE CASCADE, "
        "UNIQUE(session_id, ordinal, source))"
    )
    connection.execute(
        "CREATE INDEX search_locator_session ON search_locators(session_id)"
    )
    fts_available = "1"
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE search_fts USING fts5("
            "search_text, content='', contentless_delete=1, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
    except sqlite3.OperationalError:
        fts_available = "0"
    connection.executemany(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        (
            ("schema_version", str(SESSION_INDEX_SCHEMA_VERSION)),
            ("workspace", _workspace_identity(workspace)),
            ("latest_session_id", ""),
            ("fts_available", fts_available),
        ),
    )


def _fts_available(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'fts_available'"
    ).fetchone()
    return row is not None and row[0] == "1"


def _replace_search_documents(
    connection: sqlite3.Connection, record: SessionRecord
) -> None:
    _remove_search_documents(connection, record.session_id)
    for document in searchable_documents(record):
        cursor = connection.execute(
            "INSERT INTO search_locators (session_id, ordinal, source) VALUES (?, ?, ?)",
            (record.session_id, document.ordinal, document.source),
        )
        connection.execute(
            "INSERT INTO search_fts(rowid, search_text) VALUES (?, ?)",
            (cursor.lastrowid, document.indexed_text),
        )


def _remove_search_documents(
    connection: sqlite3.Connection, session_id: str
) -> None:
    rows = connection.execute(
        "SELECT doc_id FROM search_locators WHERE session_id = ?", (session_id,)
    ).fetchall()
    connection.executemany(
        "DELETE FROM search_fts WHERE rowid = ?", ((row[0],) for row in rows)
    )
    connection.execute("DELETE FROM search_locators WHERE session_id = ?", (session_id,))


def _catalog_values(record: SessionRecord) -> tuple[str, str | None, str, str, str, str, str]:
    return (
        record.session_id,
        record.name,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
        record.provider,
        record.model,
        "",
    )


def _entry(row: tuple[object, ...]) -> CatalogEntry:
    return CatalogEntry(
        session_id=str(row[0]),
        name=None if row[1] is None else str(row[1]),
        created_at=datetime.fromisoformat(str(row[2])),
        updated_at=datetime.fromisoformat(str(row[3])),
        provider=str(row[4]),
        model=str(row[5]),
        preview=str(row[6]),
    )


def _newest_id(records: tuple[SessionRecord, ...]) -> str | None:
    if not records:
        return None
    return max(records, key=lambda record: (record.updated_at, record.session_id)).session_id


def _resolve_hint(
    hint: str | None | Callable[[], str | None],
) -> str | None:
    return hint() if callable(hint) else hint


def _workspace_identity(workspace: Path) -> str:
    value = str(workspace)
    return os.path.normcase(value) if os.name == "nt" else value

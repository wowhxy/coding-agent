"""Temporary same-workspace recall over the shared Session search index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .session import SessionError
from .session_index import SessionIndex
from .session_store import JsonSessionStore


_PAST_REFERENCE = re.compile(
    r"上次|之前|昨天|以前|涓婃|涔嬪墠|鏄ㄥぉ|浠ュ墠|"
    r"\bprevious\b|\bearlier\b|\blast\s+time\b",
    re.IGNORECASE,
)
_MAX_RESULTS = 20


@dataclass(frozen=True, slots=True)
class RecallEntry:
    session_id: str
    source: str
    excerpt: str
    ordinal: int
    timestamp: datetime
    score: int


class RecallService:
    """Give Recall semantics to the shared locator-first retrieval layer."""

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
        canonical = Path(workspace).resolve(strict=True)
        return SessionIndex(self.store.root, canonical).database_path

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
            results = self.store.search_session_results(
                workspace,
                query,
                exclude_session_id=exclude_session_id,
                limit=limit,
                fts_enabled=self.fts_enabled,
            )
        except SessionError:
            self.backend = "scan"
            return ()
        self.backend = self.store.last_report.search_backend
        return tuple(
            RecallEntry(
                item.session_id,
                item.source,
                item.snippet,
                item.ordinal,
                item.updated_at,
                max(0, round(item.score * 1_000_000)),
            )
            for item in results
        )


def should_automatic_recall(text: str) -> bool:
    return type(text) is str and _PAST_REFERENCE.search(text) is not None

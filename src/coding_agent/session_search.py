"""Bounded search projections and canonical result materialization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from .protocol import Message, Role, ToolCall
from .session import SessionRecord


MAX_SEARCH_DOCUMENT_CHARS = 2_000
MAX_TOOL_OUTPUT_CHARS = 800
MAX_SEARCH_SNIPPET_CHARS = 500
_CREDENTIAL = re.compile(
    r"\b(?:api[_ -]?key|token|password|secret|credential)\s*[:=]\s*\S+|"
    r"\bauthorization\s*:\s*[^\r\n]+|"
    r"\bbearer\s+[A-Za-z0-9._-]{8,}|\bsk-[A-Za-z0-9]{12,}",
    re.IGNORECASE,
)
_LEXEME = re.compile(r"[^\W_]+(?:_[^\W_]+)*", re.UNICODE)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(frozen=True, slots=True)
class SearchDocument:
    ordinal: int
    source: str
    text: str

    @property
    def indexed_text(self) -> str:
        return " ".join(search_terms(self.text))


@dataclass(frozen=True, slots=True)
class SearchLocator:
    session_id: str
    ordinal: int
    source: str
    updated_at: datetime
    rank: float


@dataclass(frozen=True, slots=True)
class SessionSearchResult:
    session_id: str
    name: str | None
    updated_at: datetime
    snippet: str
    score: float
    ordinal: int
    source: str


def searchable_documents(record: SessionRecord) -> tuple[SearchDocument, ...]:
    """Extract bounded, credential-filtered units without copying full history."""

    documents: list[SearchDocument] = []
    metadata = " ".join(
        value
        for value in (
            record.session_id,
            record.session_id[:6],
            record.name,
            record.provider,
            record.model,
        )
        if value
    )
    if safe := _safe_projection(metadata):
        documents.append(SearchDocument(-1, "metadata", safe))

    calls: dict[str, ToolCall] = {}
    for ordinal, message in enumerate(record.messages):
        for call in message.tool_calls:
            calls[call.id] = call
        if message.role is Role.USER and message.content:
            _append(documents, ordinal, "user", message.content)
        elif (
            message.role is Role.ASSISTANT
            and not message.tool_calls
            and message.content
        ):
            _append(documents, ordinal, "assistant", message.content)
        elif message.role is Role.TOOL:
            projection = _tool_projection(message, calls.get(message.tool_call_id or ""))
            if projection:
                _append(documents, ordinal, "tool", projection)
    return tuple(documents)


def materialize_document(
    record: SessionRecord, ordinal: int, source: str
) -> str | None:
    if ordinal == -1 and source == "metadata":
        return _safe_projection(
            " ".join(
                value
                for value in (
                    record.session_id,
                    record.session_id[:6],
                    record.name,
                    record.provider,
                    record.model,
                )
                if value
            )
        )
    if not 0 <= ordinal < len(record.messages):
        return None
    calls: dict[str, ToolCall] = {}
    for message in record.messages[: ordinal + 1]:
        for call in message.tool_calls:
            calls[call.id] = call
    message = record.messages[ordinal]
    if source == "user" and message.role is Role.USER:
        return _safe_projection(message.content or "")
    if (
        source == "assistant"
        and message.role is Role.ASSISTANT
        and not message.tool_calls
    ):
        return _safe_projection(message.content or "")
    if source == "tool" and message.role is Role.TOOL:
        return _safe_projection(
            _tool_projection(message, calls.get(message.tool_call_id or ""))
        )
    return None


def search_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for match in _LEXEME.finditer(text.casefold()):
        value = match.group(0)
        terms.append(value)
        if "_" in value:
            terms.extend(part for part in value.split("_") if part)
    for match in _CJK.finditer(text):
        value = match.group(0)
        terms.append(value)
        terms.extend(value[index : index + 2] for index in range(len(value) - 1))
        terms.extend(value)
    return tuple(dict.fromkeys(term for term in terms if term))


def matches_document(document: SearchDocument, query: str) -> tuple[bool, float]:
    wanted = set(search_terms(query))
    available = set(search_terms(document.text))
    overlap = wanted & available
    if not overlap:
        return False, 0.0
    phrase = query.strip().casefold()
    score = float(len(overlap) * 10 + (20 if phrase in document.text.casefold() else 0))
    return True, score


def bounded_snippet(text: str) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(compact) <= MAX_SEARCH_SNIPPET_CHARS:
        return compact
    return compact[: MAX_SEARCH_SNIPPET_CHARS - 1] + "…"


def _append(
    documents: list[SearchDocument], ordinal: int, source: str, text: str
) -> None:
    if projection := _safe_projection(text):
        documents.append(SearchDocument(ordinal, source, projection))


def _safe_projection(text: str) -> str:
    if not text:
        return ""
    cleaned = _CREDENTIAL.sub("[REDACTED]", text.replace("\x00", " "))
    return cleaned[:MAX_SEARCH_DOCUMENT_CHARS]


def _tool_projection(message: Message, call: ToolCall | None) -> str:
    if call is None:
        return ""
    arguments = _tool_arguments(call)
    header = [f"tool: {call.name}"]
    for key in _argument_fields(call.name):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            header.append(f"{key}: {value[:400]}")
    try:
        result = json.loads(message.content or "")
    except (TypeError, ValueError):
        result = {}
    if isinstance(result, dict):
        ok = result.get("ok")
        if isinstance(ok, bool):
            header.append("status: " + ("ok" if ok else "failed"))
        error_code = result.get("error_code")
        error_message = result.get("error_message")
        if isinstance(error_code, str) and error_code:
            header.append(f"error_code: {error_code}")
        if isinstance(error_message, str) and error_message:
            header.append(f"error: {error_message[:400]}")
        if call.name == "execute_command":
            output = result.get("output")
            if isinstance(output, str) and output:
                header.append("output: " + output[:MAX_TOOL_OUTPUT_CHARS])
    return "\n".join(header)


def _tool_arguments(call: ToolCall) -> dict[str, object]:
    try:
        value = json.loads(call.arguments_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _argument_fields(tool_name: str) -> tuple[str, ...]:
    return {
        "list_files": ("path",),
        "search_text": ("query", "path"),
        "read_file": ("path",),
        "write_file": ("path",),
        "replace_in_file": ("path",),
        "execute_command": ("command", "cwd"),
    }.get(tool_name, ())

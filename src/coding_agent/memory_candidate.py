"""Bounded, best-effort extraction of evidence-backed workspace memory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .context import truncate_text
from .model import ModelClient
from .protocol import Message, Role


_KINDS = frozenset({"command", "constraint", "convention", "architecture", "fact"})
_SOURCES = frozenset(
    {"USER_EXPLICIT", "CONFIG_OBSERVED", "TOOL_VERIFIED", "MODEL_INFERRED"}
)
_LONG_TERM_SIGNAL = re.compile(
    r"\b(always|never|must|from now on|project uses?|keep using|do not)\b|"
    r"以后|始终|必须|约定|项目使用|从现在开始|不要修改",
    re.IGNORECASE,
)
_TRANSIENT_SIGNAL = re.compile(
    r"\b(temporary|temporarily|today|this run|debug output|current failure|"
    r"failed once|one[- ]off|hypothesis)\b|"
    r"临时|暂时|今天|本次调试|假设|当前失败",
    re.IGNORECASE,
)
_RELEVANT_COMMAND = re.compile(
    r"\b(pytest|ctest|unittest|nox|tox|cargo test|go test|npm test|"
    r"pnpm test|yarn test|mvn test|gradle\w* test|cmake|make)\b",
    re.IGNORECASE,
)
_MAX_CANDIDATES = 5
_MAX_TEXT_CHARS = 500
_MAX_EVIDENCE_CHARS = 1_000
_MAX_TRANSCRIPT_CHARS = 20_000
_MEMORY_KEY = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_CREDENTIAL_VALUE = re.compile(
    r"\b(?:api[_ -]?key|token|password|secret|credential)\s*[:=]\s*\S+|"
    r"\bauthorization\s*:\s*bearer\s+\S+|"
    r"\bbearer\s+[A-Za-z0-9._-]{8,}|\bsk-[A-Za-z0-9]{12,}",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    """Small source pointer; never contains a full tool payload."""

    user_quote: str | None = None
    tool_name: str | None = None
    path: str | None = None
    command: str | None = None
    success: bool | None = None

    def values(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (self.user_quote, self.tool_name, self.path, self.command)
            if value is not None
        )


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A model proposal which must still pass the local memory policy."""

    key: str
    content: str
    kind: str
    source: str
    evidence: MemoryEvidence = MemoryEvidence()

    @property
    def text(self) -> str:
        return f"{self.key} = {self.content}"


class MemoryCandidateExtractor:
    """Use at most one no-tools model call to propose bounded memory entries."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client
        self.last_diagnostic: str | None = None

    def extract(self, turn_messages: tuple[Message, ...]) -> tuple[MemoryCandidate, ...]:
        """Return proposals, or an empty tuple without affecting the agent turn."""

        self.last_diagnostic = None
        if not _eligible(turn_messages):
            return ()
        try:
            response = self.model_client.complete(_request(turn_messages), ())
        except Exception:
            self.last_diagnostic = "memory candidate extraction failed"
            return ()
        if response.tool_calls or not response.final_text:
            self.last_diagnostic = "memory candidate response malformed"
            return ()
        try:
            document = json.loads(response.final_text)
        except (TypeError, ValueError):
            self.last_diagnostic = "memory candidate response malformed"
            return ()
        if type(document) is not dict or set(document) != {"candidates"}:
            self.last_diagnostic = "memory candidate response malformed"
            return ()
        raw_candidates = document["candidates"]
        if type(raw_candidates) is not list:
            self.last_diagnostic = "memory candidate response malformed"
            return ()
        accepted: list[MemoryCandidate] = []
        for value in raw_candidates:
            candidate = _parse_candidate(value)
            if candidate is not None:
                accepted.append(candidate)
            if len(accepted) == _MAX_CANDIDATES:
                break
        return tuple(accepted)


def is_safe_candidate(
    candidate: MemoryCandidate, sensitive_values: tuple[str, ...]
) -> bool:
    """Reject secrets, source dumps, excessive data, and transient notes."""

    values = (candidate.key, candidate.content, *candidate.evidence.values())
    if any(len(value) > _MAX_EVIDENCE_CHARS for value in candidate.evidence.values()):
        return False
    if len(candidate.content) > _MAX_TEXT_CHARS:
        return False
    for value in values:
        if any(
            type(sensitive) is str and sensitive and sensitive in value
            for sensitive in sensitive_values
        ):
            return False
        if _CREDENTIAL_VALUE.search(value) or "```" in value:
            return False
    if _TRANSIENT_SIGNAL.search(candidate.content):
        return False
    if "\n" in candidate.content and re.search(r"[=(){};]", candidate.content):
        return False
    return True


def _eligible(messages: tuple[Message, ...]) -> bool:
    if any(
        message.role is Role.USER
        and message.content is not None
        and _LONG_TERM_SIGNAL.search(message.content)
        for message in messages
    ):
        return True
    calls = {
        call.id: call
        for message in messages
        if message.role is Role.ASSISTANT
        for call in message.tool_calls
    }
    for message in messages:
        if message.role is not Role.TOOL or message.tool_call_id not in calls:
            continue
        call = calls[message.tool_call_id]
        if not _tool_succeeded(message):
            continue
        if call.name in {"read_file", "search_text", "list_files"}:
            return True
        if call.name == "execute_command":
            try:
                arguments = json.loads(call.arguments_json)
            except (TypeError, ValueError):
                continue
            command = arguments.get("command") if type(arguments) is dict else None
            if type(command) is str and _RELEVANT_COMMAND.search(command):
                return True
    return False


def _tool_succeeded(message: Message) -> bool:
    if message.content is None:
        return False
    try:
        payload = json.loads(message.content)
    except (TypeError, ValueError):
        return False
    return type(payload) is dict and payload.get("ok") is True


def _request(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    transcript = json.dumps(
        [
            {
                "role": message.role.value,
                "content": message.content,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments_json}
                    for call in message.tool_calls
                ],
                "tool_call_id": message.tool_call_id,
            }
            for message in messages
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    transcript = truncate_text(transcript, _MAX_TRANSCRIPT_CHARS)
    return (
        Message(
            Role.SYSTEM,
            "Propose only stable workspace knowledge grounded in the supplied turn. "
            "Do not call tools and do not invent evidence. Return strict JSON: "
            '{"candidates":[{"key":str,"content":str,'
            '"kind":"command|constraint|convention|architecture|fact",'
            '"source":"USER_EXPLICIT|CONFIG_OBSERVED|TOOL_VERIFIED|MODEL_INFERRED",'
            '"evidence":{"user_quote":str|null,"tool_name":str|null,'
            '"path":str|null,"command":str|null,"success":bool|null}}]}. '
            "Use USER_EXPLICIT only for an exact quote from the current user, "
            "CONFIG_OBSERVED only for current read/search/list evidence, and "
            "TOOL_VERIFIED only for a successful current command. MODEL_INFERRED "
            "is allowed as a proposal but will not be persisted. Return at most "
            "five concise items and an empty list when nothing is durable. Never "
            "include credentials, full source, logs, or temporary failures.",
        ),
        Message(Role.USER, transcript),
    )


def _parse_candidate(value: Any) -> MemoryCandidate | None:
    if type(value) is not dict or set(value) != {
        "key", "content", "kind", "source", "evidence"
    }:
        return None
    key, content, kind, source = (
        value["key"], value["content"], value["kind"], value["source"]
    )
    if any(type(field) is not str for field in (key, content, kind, source)):
        return None
    key = key.strip()
    content = content.strip()
    if (
        not key
        or len(key) > 80
        or _MEMORY_KEY.fullmatch(key) is None
        or not content
        or len(content) > _MAX_TEXT_CHARS
        or kind not in _KINDS
        or source not in _SOURCES
    ):
        return None
    evidence = _parse_evidence(value["evidence"])
    if evidence is None:
        return None
    candidate = MemoryCandidate(key, content, kind, source, evidence)
    return candidate if is_safe_candidate(candidate, ()) else None


def _parse_evidence(value: Any) -> MemoryEvidence | None:
    fields = {"user_quote", "tool_name", "path", "command", "success"}
    if type(value) is not dict or not set(value).issubset(fields):
        return None
    normalized = {field: value.get(field) for field in fields}
    if any(
        item is not None and type(item) is not str
        for field, item in normalized.items()
        if field != "success"
    ):
        return None
    if normalized["success"] is not None and type(normalized["success"]) is not bool:
        return None
    for field in ("user_quote", "tool_name", "path", "command"):
        item = normalized[field]
        if item is not None:
            item = item.strip()
            if not item or len(item) > _MAX_EVIDENCE_CHARS:
                return None
            normalized[field] = item
    return MemoryEvidence(**normalized)

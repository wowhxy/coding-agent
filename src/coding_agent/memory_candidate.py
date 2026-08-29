"""Best-effort extraction of user-confirmable workspace-memory candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .context import truncate_text
from .model import ModelClient
from .protocol import Message, Role


_KINDS = frozenset({"command", "constraint", "convention", "architecture", "fact"})
_SOURCES = frozenset({"user", "observed"})
_LONG_TERM_SIGNAL = re.compile(
    r"\b(always|never|must|from now on|project uses?|keep using)\b|"
    r"以后|始终|必须|约定|项目使用|从现在开始",
    re.IGNORECASE,
)
_TRANSIENT_SIGNAL = re.compile(
    r"\b(temporary|temporarily|today|this run|debug output)\b|临时|暂时|今天|本次调试",
    re.IGNORECASE,
)
_MAX_CANDIDATES = 4
_MAX_TEXT_CHARS = 500
_MAX_TRANSCRIPT_CHARS = 20_000
_CREDENTIAL_VALUE = re.compile(
    r"\b(?:api[_ -]?key|token|password|secret|credential)\s*[:=]\s*\S+|"
    r"\bbearer\s+[A-Za-z0-9._-]{8,}|\bsk-[A-Za-z0-9]{12,}",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A locally validated fact that still requires explicit user approval."""

    text: str
    kind: str
    source: str


class MemoryCandidateExtractor:
    """Use a normal model call without tools to propose bounded memory entries."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def extract(self, turn_messages: tuple[Message, ...]) -> tuple[MemoryCandidate, ...]:
        """Return safe candidates, or an empty tuple on ineligibility/failure."""

        if not _eligible(turn_messages):
            return ()
        try:
            response = self.model_client.complete(_request(turn_messages), ())
            if response.tool_calls or not response.final_text:
                return ()
            document = json.loads(response.final_text)
            if type(document) is not dict or set(document) != {"candidates"}:
                return ()
            raw_candidates = document["candidates"]
            if type(raw_candidates) is not list:
                return ()
            accepted: list[MemoryCandidate] = []
            for value in raw_candidates:
                candidate = _parse_candidate(value)
                if candidate is not None:
                    accepted.append(candidate)
                if len(accepted) == _MAX_CANDIDATES:
                    break
            return tuple(accepted)
        except Exception:
            return ()


def is_safe_candidate(
    candidate: MemoryCandidate, sensitive_values: tuple[str, ...]
) -> bool:
    """Reject obvious credential values and source-code-shaped candidates."""

    text = candidate.text
    if any(
        type(sensitive) is str and sensitive and sensitive in text
        for sensitive in sensitive_values
    ):
        return False
    if _CREDENTIAL_VALUE.search(text) or "```" in text:
        return False
    if "\n" in text and re.search(r"[=(){};]", text):
        return False
    return True


def _eligible(messages: tuple[Message, ...]) -> bool:
    if any(message.role is Role.TOOL or message.tool_calls for message in messages):
        return True
    return any(
        message.role is Role.USER
        and message.content is not None
        and _LONG_TERM_SIGNAL.search(message.content)
        for message in messages
    )


def _request(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    transcript = json.dumps(
        [
            {
                "role": message.role.value,
                "content": message.content,
                "tools": [call.name for call in message.tool_calls],
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
            "Extract only stable workspace facts worth remembering across sessions. "
            "Do not call tools. Return strict JSON: {\"candidates\":[{\"text\":str,"
            "\"kind\":\"command|constraint|convention|architecture|fact\","
            "\"source\":\"user|observed\"}]}. Return at most four items and use "
            "an empty list when nothing is durable.",
        ),
        Message(Role.USER, transcript),
    )


def _parse_candidate(value: Any) -> MemoryCandidate | None:
    if type(value) is not dict or set(value) != {"text", "kind", "source"}:
        return None
    text, kind, source = value["text"], value["kind"], value["source"]
    if any(type(field) is not str for field in (text, kind, source)):
        return None
    text = text.strip()
    if not text or len(text) > _MAX_TEXT_CHARS:
        return None
    if kind not in _KINDS or source not in _SOURCES:
        return None
    if _TRANSIENT_SIGNAL.search(text) or "```" in text:
        return None
    if "\n" in text and re.search(r"[=(){};]", text):
        return None
    return MemoryCandidate(text, kind, source)

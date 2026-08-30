"""Local evidence validation and automatic workspace-memory decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol

from .memory import MemoryItem, MemoryMatch, WorkspaceMemoryStore
from .memory_candidate import MemoryCandidate, MemoryEvidence, is_safe_candidate
from .protocol import Message, Role, ToolCall
from .session import SessionError


class MemoryAction(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    IGNORE = "IGNORE"


@dataclass(frozen=True, slots=True)
class MemoryDecision:
    action: MemoryAction
    reason: str


@dataclass(frozen=True, slots=True)
class MemoryChange:
    action: MemoryAction
    item: MemoryItem


@dataclass(frozen=True, slots=True)
class MemoryManagementReport:
    changes: tuple[MemoryChange, ...] = ()
    ignored: int = 0
    diagnostic: str | None = None


class CandidateExtractor(Protocol):
    last_diagnostic: str | None

    def extract(self, messages: tuple[Message, ...]) -> tuple[MemoryCandidate, ...]: ...


_SOURCE_STRENGTH = {
    "MODEL_INFERRED": 0,
    "observed": 1,
    # Legacy confirmed candidates had explicit user approval. Preserve that
    # authority even though older records did not retain the original evidence.
    "confirmed_candidate": 4,
    "TOOL_VERIFIED": 2,
    "CONFIG_OBSERVED": 3,
    "user": 4,
    "USER_EXPLICIT": 4,
}
_DISTINCTIVE_TOKEN = re.compile(r"[a-z0-9_.+/-]+", re.IGNORECASE)
_CONFIG_FILE = re.compile(
    r"(?:^|/)(?:CMakeLists\.txt|pyproject\.toml|package\.json|Cargo\.toml|"
    r"go\.mod|Makefile|setup\.cfg|tox\.ini|\.editorconfig|requirements[^/]*)$",
    re.IGNORECASE,
)
_DURABLE_COMMAND_KEY = re.compile(
    r"^(?:build|test|check|lint|format|typecheck)\.command$"
)
_DURABLE_COMMAND = re.compile(
    r"\b(pytest|ctest|unittest|nox|tox|cargo test|go test|npm test|"
    r"pnpm test|yarn test|mvn test|gradle\w* test|cmake|make|ruff|mypy)\b",
    re.IGNORECASE,
)


class MemoryPolicy:
    """Make deterministic ADD/UPDATE/IGNORE decisions from canonical turn data."""

    def __init__(self, sensitive_values: tuple[str, ...]) -> None:
        self.sensitive_values = sensitive_values

    def decide(
        self,
        candidate: MemoryCandidate,
        turn_messages: tuple[Message, ...],
        *,
        match_status: str,
        existing: MemoryItem | None = None,
    ) -> MemoryDecision:
        if not is_safe_candidate(candidate, self.sensitive_values):
            return MemoryDecision(MemoryAction.IGNORE, "unsafe or transient candidate")
        if not _valid_evidence(candidate, turn_messages):
            return MemoryDecision(MemoryAction.IGNORE, "evidence not found in current turn")
        if match_status in {"exact_duplicate", "normalized_duplicate"}:
            return MemoryDecision(MemoryAction.IGNORE, "duplicate")
        if match_status == "new":
            return MemoryDecision(MemoryAction.ADD, "new evidence-backed memory")
        if match_status != "conflict" or existing is None:
            return MemoryDecision(MemoryAction.IGNORE, "invalid memory match")
        old_strength = _SOURCE_STRENGTH.get(existing.source, 0)
        new_strength = _SOURCE_STRENGTH.get(candidate.source, 0)
        if new_strength < old_strength:
            return MemoryDecision(MemoryAction.IGNORE, "weaker evidence cannot replace memory")
        return MemoryDecision(MemoryAction.UPDATE, "newer equal-or-stronger evidence")


class MemoryAutoManager:
    """Run extraction, policy, and persistence without changing task outcomes."""

    def __init__(
        self,
        extractor: CandidateExtractor,
        store: WorkspaceMemoryStore,
        sensitive_values: tuple[str, ...],
    ) -> None:
        self.extractor = extractor
        self.store = store
        self.sensitive_values = sensitive_values
        self.policy = MemoryPolicy(sensitive_values)

    def process(
        self, workspace: Path, turn_messages: tuple[Message, ...]
    ) -> MemoryManagementReport:
        try:
            candidates = self.extractor.extract(turn_messages)
        except Exception:
            return MemoryManagementReport(diagnostic="memory candidate extraction failed")
        diagnostic = getattr(self.extractor, "last_diagnostic", None)
        changes: list[MemoryChange] = []
        ignored = 0
        for candidate in candidates[:5]:
            try:
                preflight = self.policy.decide(
                    candidate,
                    turn_messages,
                    match_status="new",
                )
                if preflight.action is MemoryAction.IGNORE:
                    ignored += 1
                    continue
                match = self.store.match(
                    workspace,
                    candidate.content,
                    candidate.kind,
                    key=candidate.key,
                )
                decision = self.policy.decide(
                    candidate,
                    turn_messages,
                    match_status=match.status,
                    existing=match.existing,
                )
                if decision.action is MemoryAction.IGNORE:
                    ignored += 1
                    continue
                item = self._persist(workspace, candidate, match, decision.action)
                changes.append(MemoryChange(decision.action, item))
            except SessionError:
                return MemoryManagementReport(
                    tuple(changes), ignored, "workspace memory update failed"
                )
            except Exception:
                return MemoryManagementReport(
                    tuple(changes), ignored, "workspace memory policy failed"
                )
        return MemoryManagementReport(tuple(changes), ignored, diagnostic)

    def _persist(
        self,
        workspace: Path,
        candidate: MemoryCandidate,
        match: MemoryMatch,
        action: MemoryAction,
    ) -> MemoryItem:
        if action is MemoryAction.UPDATE and match.existing is not None:
            return self.store.replace(
                workspace,
                match.existing.id,
                candidate.content,
                self.sensitive_values,
                kind=candidate.kind,
                source=candidate.source,
                key=candidate.key,
            )
        return self.store.add(
            workspace,
            candidate.content,
            self.sensitive_values,
            kind=candidate.kind,
            source=candidate.source,
            key=candidate.key,
        )


@dataclass(frozen=True, slots=True)
class _ToolObservation:
    call: ToolCall
    arguments: dict[str, object]
    output: str
    ok: bool


def _valid_evidence(
    candidate: MemoryCandidate, messages: tuple[Message, ...]
) -> bool:
    source = candidate.source
    evidence = candidate.evidence
    if source == "MODEL_INFERRED":
        return False
    if source == "USER_EXPLICIT":
        return _valid_user_evidence(candidate, evidence, messages)
    observations = _tool_observations(messages)
    if source == "CONFIG_OBSERVED":
        return any(
            _valid_config_observation(candidate, evidence, observation)
            for observation in observations
        )
    if source == "TOOL_VERIFIED":
        return any(
            _valid_command_observation(candidate, evidence, observation)
            for observation in observations
        )
    return False


def _valid_user_evidence(
    candidate: MemoryCandidate,
    evidence: MemoryEvidence,
    messages: tuple[Message, ...],
) -> bool:
    quote = evidence.user_quote
    if quote is None:
        return False
    if not any(
        message.role is Role.USER
        and message.content is not None
        and quote in message.content
        for message in messages
    ):
        return False
    content_tokens = _tokens(candidate.content)
    quote_tokens = _tokens(quote)
    return bool(content_tokens) and len(content_tokens & quote_tokens) >= max(
        1, min(2, len(content_tokens))
    )


def _valid_config_observation(
    candidate: MemoryCandidate,
    evidence: MemoryEvidence,
    observation: _ToolObservation,
) -> bool:
    if not observation.ok or observation.call.name not in {
        "read_file", "search_text", "list_files"
    }:
        return False
    if evidence.tool_name != observation.call.name or evidence.success is not True:
        return False
    actual_path = _argument_path(observation.arguments)
    if evidence.path is None or not _same_path(evidence.path, actual_path):
        return False
    normalized_path = actual_path.replace("\\", "/")
    if observation.call.name in {"read_file", "search_text"} and not _CONFIG_FILE.search(
        normalized_path
    ):
        return False
    if observation.call.name == "list_files" and not candidate.key.endswith(".root"):
        return False
    haystack = f"{actual_path}\n{observation.output}"
    if candidate.key == "build.system" and candidate.content.casefold() == "cmake":
        return "cmake" in haystack.casefold()
    if candidate.key.endswith(".root"):
        root = candidate.content.strip().replace("\\", "/").rstrip("/")
        normalized_haystack = haystack.replace("\\", "/")
        if root and re.search(
            rf"(?m)(?:^|/)" + re.escape(root) + r"(?:/|$)",
            normalized_haystack,
            re.IGNORECASE,
        ):
            return True
    candidate_tokens = _tokens(candidate.content)
    return bool(candidate_tokens & _tokens(haystack))


def _valid_command_observation(
    candidate: MemoryCandidate,
    evidence: MemoryEvidence,
    observation: _ToolObservation,
) -> bool:
    if (
        not observation.ok
        or observation.call.name != "execute_command"
        or evidence.tool_name != "execute_command"
        or evidence.success is not True
        or evidence.command is None
        or candidate.kind != "command"
        or _DURABLE_COMMAND_KEY.fullmatch(candidate.key) is None
        or _DURABLE_COMMAND.search(evidence.command) is None
    ):
        return False
    command = observation.arguments.get("command")
    if type(command) is not str or _normalize_space(command) != _normalize_space(
        evidence.command
    ):
        return False
    return _normalize_space(candidate.content).casefold() in _normalize_space(
        command
    ).casefold()


def _tool_observations(messages: tuple[Message, ...]) -> tuple[_ToolObservation, ...]:
    calls = {
        call.id: call
        for message in messages
        if message.role is Role.ASSISTANT
        for call in message.tool_calls
    }
    observations: list[_ToolObservation] = []
    for message in messages:
        if message.role is not Role.TOOL or message.tool_call_id not in calls:
            continue
        call = calls[message.tool_call_id]
        try:
            arguments = json.loads(call.arguments_json)
            result = json.loads(message.content or "")
        except (TypeError, ValueError):
            continue
        if type(arguments) is not dict or type(result) is not dict:
            continue
        output = result.get("output")
        ok = result.get("ok")
        if type(output) is not str or type(ok) is not bool:
            continue
        observations.append(_ToolObservation(call, arguments, output, ok))
    return tuple(observations)


def _argument_path(arguments: dict[str, object]) -> str:
    for field in ("path", "root", "directory"):
        value = arguments.get(field)
        if type(value) is str:
            return value
    return "."


def _same_path(expected: str, actual: str) -> bool:
    return str(PurePosixPath(expected.replace("\\", "/"))).casefold() == str(
        PurePosixPath(actual.replace("\\", "/"))
    ).casefold()


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _DISTINCTIVE_TOKEN.findall(text):
        token = raw.strip("._+-/").casefold()
        if len(token) > 1 and token not in {"the", "this", "for", "use", "run"}:
            tokens.add(token)
    return tokens


def _normalize_space(value: str) -> str:
    return " ".join(value.split())

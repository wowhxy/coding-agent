"""Pre-turn automatic Skill selection and bounded activation composition."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .model import ModelClient, ModelClientError
from .protocol import Message, Role
from .skills import (
    MAX_ACTIVE_SKILL_BODY_CHARS,
    MAX_ACTIVE_SKILLS,
    ActiveSkill,
    SkillDiagnostic,
    SkillError,
    SkillMetadata,
    SkillRegistry,
)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    names: tuple[str, ...]
    diagnostic: SkillDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class SkillActivationResult:
    skills: tuple[ActiveSkill, ...]
    diagnostics: tuple[SkillDiagnostic, ...] = ()


class SkillSelector:
    """Ask the configured model for Skill names using metadata only."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def select(
        self,
        task: str,
        metadata: tuple[SkillMetadata, ...],
        *,
        limit: int = MAX_ACTIVE_SKILLS,
    ) -> SkillSelection:
        if not metadata or limit <= 0:
            return SkillSelection(())
        effective_limit = min(limit, MAX_ACTIVE_SKILLS)
        payload = {
            "task": task,
            "available_skills": [
                {
                    "name": item.name,
                    "description": item.description,
                    "scope": item.scope,
                }
                for item in metadata
            ],
            "maximum": effective_limit,
        }
        messages = (
            Message(
                Role.SYSTEM,
                "Select only useful Skills for this task. Return strict JSON with "
                'exactly one key: {"skills":["name"]}. Do not call tools.',
            ),
            Message(
                Role.USER,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        try:
            turn = self.model_client.complete(messages, ())
            if turn.tool_calls or turn.final_text is None or not turn.final_text.strip():
                return self._failure()
            decoded = json.loads(turn.final_text)
        except (ModelClientError, json.JSONDecodeError, TypeError, ValueError):
            return self._failure()
        if type(decoded) is not dict or set(decoded) != {"skills"}:
            return self._failure()
        names = decoded["skills"]
        if type(names) is not list or any(type(name) is not str for name in names):
            return self._failure()

        available = {item.name for item in metadata}
        selected: list[str] = []
        for name in names:
            if name in available and name not in selected:
                selected.append(name)
                if len(selected) == effective_limit:
                    break
        return SkillSelection(tuple(selected))

    @staticmethod
    def _failure() -> SkillSelection:
        return SkillSelection(
            (),
            SkillDiagnostic(
                "SKILL_SELECTOR_FAILED",
                "Automatic Skill selection failed; continuing without automatic Skills.",
            ),
        )


class SkillActivator:
    """Merge manual pins and one automatic selection into bounded active Skills."""

    def __init__(self, registry: SkillRegistry, selector: SkillSelector) -> None:
        self.registry = registry
        self.selector = selector

    def prepare(
        self, task: str, manual_names: tuple[str, ...] = ()
    ) -> SkillActivationResult:
        diagnostics: list[SkillDiagnostic] = []
        active: list[ActiveSkill] = []
        body_chars = 0
        ordered_manual = tuple(dict.fromkeys(manual_names))[:MAX_ACTIVE_SKILLS]

        for name in ordered_manual:
            try:
                skill = self.registry.load(name)
            except SkillError:
                diagnostics.append(self._activation_failure())
                continue
            if body_chars + len(skill.body) > MAX_ACTIVE_SKILL_BODY_CHARS:
                diagnostics.append(self._activation_failure())
                continue
            active.append(ActiveSkill(skill, "manual"))
            body_chars += len(skill.body)

        capacity = MAX_ACTIVE_SKILLS - len(ordered_manual)
        manual_set = set(ordered_manual)
        eligible = tuple(
            item for item in self.registry.metadata if item.name not in manual_set
        )
        selection = self.selector.select(task, eligible, limit=capacity)
        if selection.diagnostic is not None:
            diagnostics.append(selection.diagnostic)
        for name in selection.names:
            try:
                skill = self.registry.load(name)
            except SkillError:
                diagnostics.append(self._activation_failure())
                continue
            if body_chars + len(skill.body) > MAX_ACTIVE_SKILL_BODY_CHARS:
                diagnostics.append(self._activation_failure())
                continue
            active.append(ActiveSkill(skill, "automatic"))
            body_chars += len(skill.body)

        return SkillActivationResult(tuple(active), tuple(diagnostics))

    @staticmethod
    def _activation_failure() -> SkillDiagnostic:
        return SkillDiagnostic(
            "SKILL_ACTIVATION_FAILED",
            "A selected Skill could not be activated; continuing without it.",
        )

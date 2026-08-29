"""Declarative, text-only Skill discovery and activation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal


MAX_SKILL_FRONT_MATTER_CHARS = 4_096
MAX_SKILL_FRONT_MATTER_LINES = 32
MAX_SKILL_BODY_CHARS = 10_000
MAX_ACTIVE_SKILL_BODY_CHARS = 20_000
MAX_ACTIVE_SKILLS = 3

# Allow the conventional blank line and line ending around a maximum-length
# UTF-8 body while still bounding the activation read.
_MAX_SKILL_BODY_BYTES = (MAX_SKILL_BODY_CHARS + 4) * 4
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SCOPES = ("user", "workspace")

SkillScope = Literal["user", "workspace"]
SkillActivation = Literal["manual", "automatic"]


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    scope: SkillScope
    path: Path


@dataclass(frozen=True, slots=True)
class Skill:
    metadata: SkillMetadata
    body: str


@dataclass(frozen=True, slots=True)
class ActiveSkill:
    skill: Skill
    activation: SkillActivation


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    code: str
    message: str
    path: Path | None = None


class SkillError(Exception):
    """A stable, safe Skill subsystem error."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class SkillRegistry:
    """Discover effective Skill metadata and lazily load instruction bodies."""

    def __init__(self, home: Path, workspace: Path) -> None:
        self._home = Path(home).resolve(strict=False)
        self._workspace = Path(workspace).resolve(strict=True)
        self._roots: dict[SkillScope, Path] = {
            "user": self._home / "skills",
            "workspace": self._workspace / ".coding-agent" / "skills",
        }
        self._effective: dict[str, SkillMetadata] = {}
        self._diagnostics: tuple[SkillDiagnostic, ...] = ()
        self._discovered = False

    @property
    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return self._diagnostics

    @property
    def metadata(self) -> tuple[SkillMetadata, ...]:
        if not self._discovered:
            return self.discover()
        return tuple(self._effective[name] for name in sorted(self._effective))

    def discover(self) -> tuple[SkillMetadata, ...]:
        diagnostics: list[SkillDiagnostic] = []
        valid: dict[SkillScope, list[SkillMetadata]] = {
            "user": [],
            "workspace": [],
        }

        for scope in _SCOPES:
            typed_scope: SkillScope = scope  # type: ignore[assignment]
            root = self._roots[typed_scope]
            base = self._home if scope == "user" else self._workspace
            if not root.exists() and not root.is_symlink():
                continue
            if self._has_symlink_component(base, root):
                diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", root))
                continue
            try:
                canonical_root = root.resolve(strict=True)
                if not canonical_root.is_relative_to(base) or not canonical_root.is_dir():
                    diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", root))
                    continue
                entries = sorted(root.iterdir(), key=lambda item: item.name)
            except OSError:
                diagnostics.append(self._diagnostic("SKILL_READ_FAILED", root))
                continue

            for package in entries:
                if package.is_symlink():
                    diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", package))
                    continue
                try:
                    if not package.is_dir():
                        continue
                    canonical_package = package.resolve(strict=True)
                except OSError:
                    diagnostics.append(self._diagnostic("SKILL_READ_FAILED", package))
                    continue
                if not canonical_package.is_relative_to(canonical_root):
                    diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", package))
                    continue

                path = package / "SKILL.md"
                if not path.exists() and not path.is_symlink():
                    continue
                if path.is_symlink():
                    diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", path))
                    continue
                try:
                    canonical_path = path.resolve(strict=True)
                except OSError:
                    diagnostics.append(self._diagnostic("SKILL_READ_FAILED", path))
                    continue
                if not canonical_path.is_file() or not canonical_path.is_relative_to(
                    canonical_root
                ):
                    diagnostics.append(self._diagnostic("SKILL_UNSAFE_PATH", path))
                    continue
                try:
                    name, description = self._read_metadata(canonical_path)
                except SkillError as exc:
                    diagnostics.append(
                        SkillDiagnostic(exc.error_code, exc.message, canonical_path)
                    )
                    continue
                valid[typed_scope].append(
                    SkillMetadata(name, description, typed_scope, canonical_path)
                )

        unique: dict[SkillScope, dict[str, SkillMetadata]] = {
            "user": {},
            "workspace": {},
        }
        for scope in _SCOPES:
            typed_scope = scope  # type: ignore[assignment]
            grouped: dict[str, list[SkillMetadata]] = {}
            for metadata in valid[typed_scope]:
                grouped.setdefault(metadata.name, []).append(metadata)
            for name in sorted(grouped):
                matches = grouped[name]
                if len(matches) > 1:
                    diagnostics.extend(
                        self._diagnostic("SKILL_DUPLICATE_NAME", item.path)
                        for item in matches
                    )
                else:
                    unique[typed_scope][name] = matches[0]

        effective = dict(unique["user"])
        effective.update(unique["workspace"])
        self._effective = effective
        self._diagnostics = tuple(diagnostics)
        self._discovered = True
        return tuple(effective[name] for name in sorted(effective))

    def load(self, name: str) -> Skill:
        if not self._discovered:
            self.discover()
        metadata = self._effective.get(name)
        if metadata is None:
            raise SkillError("SKILL_NOT_FOUND", "The requested Skill is not available.")

        root = self._roots[metadata.scope]
        base = self._home if metadata.scope == "user" else self._workspace
        path = metadata.path
        if (
            self._has_symlink_component(base, root)
            or path.parent.is_symlink()
            or path.is_symlink()
        ):
            raise SkillError("SKILL_UNSAFE_PATH", "The Skill path is not safe to read.")
        try:
            canonical_root = root.resolve(strict=True)
            canonical_path = path.resolve(strict=True)
        except OSError as exc:
            raise SkillError("SKILL_READ_FAILED", "The Skill could not be read.") from exc
        if not canonical_root.is_relative_to(base) or not canonical_path.is_relative_to(
            canonical_root
        ):
            raise SkillError("SKILL_UNSAFE_PATH", "The Skill path is not safe to read.")

        try:
            with canonical_path.open("rb") as stream:
                parsed_name, parsed_description = self._parse_front_matter(stream)
                raw_body = stream.read(_MAX_SKILL_BODY_BYTES + 1)
        except SkillError:
            raise
        except OSError as exc:
            raise SkillError("SKILL_READ_FAILED", "The Skill could not be read.") from exc

        if parsed_name != metadata.name or parsed_description != metadata.description:
            raise SkillError("SKILL_CHANGED", "The Skill changed after discovery.")
        if len(raw_body) > _MAX_SKILL_BODY_BYTES:
            raise SkillError(
                "SKILL_BODY_TOO_LARGE", "The Skill instruction body exceeds its limit."
            )
        try:
            body = raw_body.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SkillError("SKILL_INVALID_UTF8", "The Skill is not valid UTF-8.") from exc
        if not body:
            raise SkillError("SKILL_EMPTY_BODY", "The Skill instruction body is empty.")
        if len(body) > MAX_SKILL_BODY_CHARS:
            raise SkillError(
                "SKILL_BODY_TOO_LARGE", "The Skill instruction body exceeds its limit."
            )
        return Skill(metadata, body)

    @staticmethod
    def _has_symlink_component(base: Path, target: Path) -> bool:
        try:
            relative = target.relative_to(base)
        except ValueError:
            return True
        current = base
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    @staticmethod
    def _diagnostic(code: str, path: Path) -> SkillDiagnostic:
        messages = {
            "SKILL_UNSAFE_PATH": "Skipped a Skill package with an unsafe path.",
            "SKILL_READ_FAILED": "Skipped a Skill package that could not be read.",
            "SKILL_DUPLICATE_NAME": "Skipped a duplicate Skill name in the same scope.",
        }
        return SkillDiagnostic(code, messages[code], path)

    @classmethod
    def _read_metadata(cls, path: Path) -> tuple[str, str]:
        try:
            with path.open("rb") as stream:
                return cls._parse_front_matter(stream)
        except SkillError:
            raise
        except OSError as exc:
            raise SkillError("SKILL_READ_FAILED", "The Skill could not be read.") from exc

    @classmethod
    def _parse_front_matter(cls, stream: BinaryIO) -> tuple[str, str]:
        first = stream.readline(MAX_SKILL_FRONT_MATTER_CHARS + 2)
        if not first:
            raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")
        first_text = cls._decode_metadata_line(first)
        if first_text.rstrip("\r\n") != "---":
            raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")

        characters = len(first_text)
        lines = 1
        values: dict[str, str] = {}
        while True:
            raw = stream.readline(MAX_SKILL_FRONT_MATTER_CHARS + 2)
            if not raw:
                raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")
            text = cls._decode_metadata_line(raw)
            lines += 1
            characters += len(text)
            if (
                lines > MAX_SKILL_FRONT_MATTER_LINES
                or characters > MAX_SKILL_FRONT_MATTER_CHARS
            ):
                raise SkillError(
                    "SKILL_METADATA_TOO_LARGE", "Skill metadata exceeds its limit."
                )
            line = text.rstrip("\r\n")
            if line == "---":
                break
            if not line:
                continue
            if ":" not in line:
                raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")
            key, value = line.split(":", 1)
            value = value.strip()
            if key not in {"name", "description"} or key in values or not value:
                raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")
            values[key] = value

        if set(values) != {"name", "description"}:
            raise SkillError("SKILL_INVALID_METADATA", "Skill metadata is malformed.")
        name = values["name"]
        description = values["description"]
        if not _NAME_PATTERN.fullmatch(name) or len(name) > 64:
            raise SkillError("SKILL_INVALID_NAME", "The Skill name is invalid.")
        if (
            len(description) > 300
            or description != description.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in description)
        ):
            raise SkillError(
                "SKILL_INVALID_DESCRIPTION", "The Skill description is invalid."
            )
        return name, description

    @staticmethod
    def _decode_metadata_line(raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError("SKILL_INVALID_UTF8", "The Skill is not valid UTF-8.") from exc


class ManualSkillState:
    """Ordered manual Skill pins scoped to session IDs for one process."""

    def __init__(self) -> None:
        self._names_by_session: dict[str, tuple[str, ...]] = {}

    def names(self, session_id: str) -> tuple[str, ...]:
        return self._names_by_session.get(session_id, ())

    def use(self, session_id: str, name: str, registry: SkillRegistry) -> None:
        current = self.names(session_id)
        if name in current:
            return
        proposed = current + (name,)
        if len(proposed) > MAX_ACTIVE_SKILLS:
            raise SkillError(
                "SKILL_LIMIT", "At most three Skills can be active for one task."
            )
        loaded = tuple(registry.load(item) for item in proposed)
        if sum(len(item.body) for item in loaded) > MAX_ACTIVE_SKILL_BODY_CHARS:
            raise SkillError(
                "SKILL_ACTIVE_BODY_LIMIT",
                "The active Skill instruction bodies exceed their combined limit.",
            )
        self._names_by_session[session_id] = proposed

    def off(self, session_id: str, name: str) -> None:
        current = self.names(session_id)
        if name not in current:
            raise SkillError("SKILL_NOT_ACTIVE", "The requested Skill is not active.")
        remaining = tuple(item for item in current if item != name)
        if remaining:
            self._names_by_session[session_id] = remaining
        else:
            self._names_by_session.pop(session_id, None)

    def clear(self, session_id: str) -> None:
        self._names_by_session.pop(session_id, None)

    def remove_session(self, session_id: str) -> None:
        self.clear(session_id)

    def active(
        self, session_id: str, registry: SkillRegistry
    ) -> tuple[ActiveSkill, ...]:
        return tuple(
            ActiveSkill(registry.load(name), "manual")
            for name in self.names(session_id)
        )

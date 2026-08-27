"""Resolved workspace containment for all local file tools."""

from __future__ import annotations

from pathlib import Path


class WorkspacePathError(ValueError):
    """A recoverable workspace path failure with a stable error code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class WorkspacePaths:
    """Resolve user paths while keeping their final targets in one workspace."""

    def __init__(self, root: Path) -> None:
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("workspace root must be an existing directory")
        self.root = resolved_root

    def resolve_existing(self, relative_path: str | Path) -> Path:
        """Resolve an existing path and reject absolute or escaping inputs."""

        candidate = self._candidate(relative_path)
        unresolved_target = candidate.resolve(strict=False)
        self._require_contained(unresolved_target)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspacePathError(
                "FILE_NOT_FOUND",
                f"path does not exist: {relative_path}",
            ) from exc
        self._require_contained(resolved)
        return resolved

    def resolve_new_file(self, relative_path: str | Path) -> Path:
        """Resolve a file target whose existing parent must remain contained."""

        candidate = self._candidate(relative_path)
        resolved_candidate = candidate.resolve(strict=False)
        self._require_contained(resolved_candidate)
        try:
            resolved_parent = candidate.parent.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspacePathError(
                "FILE_NOT_FOUND",
                f"parent directory does not exist: {relative_path}",
            ) from exc
        self._require_contained(resolved_parent)
        if not resolved_parent.is_dir():
            raise WorkspacePathError(
                "NOT_A_FILE",
                f"parent path is not a directory: {relative_path}",
            )
        return resolved_candidate

    def display_path(self, path: Path) -> str:
        """Render a contained path relative to the workspace with POSIX separators."""

        candidate = path if path.is_absolute() else self.root / path
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(
                "PATH_OUTSIDE_WORKSPACE",
                "path resolves outside the workspace",
            ) from exc
        return relative.as_posix()

    def _candidate(self, relative_path: str | Path) -> Path:
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise WorkspacePathError(
                "PATH_OUTSIDE_WORKSPACE",
                "absolute paths are not allowed",
            )
        return self.root / supplied

    def _require_contained(self, candidate: Path) -> None:
        if not candidate.is_relative_to(self.root):
            raise WorkspacePathError(
                "PATH_OUTSIDE_WORKSPACE",
                "path resolves outside the workspace",
            )

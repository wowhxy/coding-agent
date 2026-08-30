"""Credential-safe, provider-offline product readiness diagnostics."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..plugins import PluginManager
from ..skills import SkillRegistry
from ..tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("diagnostic name is required")
        if type(self.ok) is not bool:
            raise TypeError("diagnostic status must be boolean")
        if type(self.detail) is not str or not self.detail.strip():
            raise ValueError("diagnostic detail is required")


def run_doctor(
    *,
    workspace: Path,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key_env: str,
    environ: Mapping[str, str],
    session_home: Path,
) -> tuple[DiagnosticCheck, ...]:
    """Run local checks without constructing a provider client or printing secrets."""

    checks: list[DiagnosticCheck] = []
    version = sys.version_info
    python_ok = version >= (3, 11)
    checks.append(
        DiagnosticCheck(
            "Python",
            python_ok,
            f"{version.major}.{version.minor}.{version.micro}"
            if python_ok
            else "Python 3.11 or newer is required",
        )
    )

    candidate = Path(workspace)
    try:
        resolved_workspace = candidate.resolve(strict=True)
        workspace_ok = resolved_workspace.is_dir() and os.access(
            resolved_workspace, os.W_OK
        )
    except (OSError, RuntimeError):
        resolved_workspace = candidate.resolve(strict=False)
        workspace_ok = False
    checks.append(
        DiagnosticCheck(
            "Workspace",
            workspace_ok,
            str(resolved_workspace)
            if workspace_ok
            else f"missing or not writable: {resolved_workspace}",
        )
    )

    provider_ok = bool(
        type(provider) is str
        and provider.strip()
        and type(model) is str
        and model.strip()
        and type(base_url) is str
        and base_url.strip()
    )
    checks.append(
        DiagnosticCheck(
            "Provider",
            provider_ok,
            f"{provider}; model {model}"
            if provider_ok
            else "base URL or model is missing",
        )
    )
    credential_ok = bool(
        type(api_key_env) is str
        and api_key_env.strip()
        and type(environ.get(api_key_env)) is str
        and environ.get(api_key_env)
    )
    checks.append(
        DiagnosticCheck(
            "Credential",
            credential_ok,
            "configured" if credential_ok else f"missing in {api_key_env}",
        )
    )
    git_path = shutil.which("git")
    checks.append(
        DiagnosticCheck(
            "Git",
            git_path is not None,
            "available" if git_path is not None else "not found on PATH",
        )
    )

    home = Path(session_home)
    resolved_home = home.resolve(strict=False)
    unsafe_home = workspace_ok and resolved_home.is_relative_to(resolved_workspace)
    if unsafe_home:
        checks.append(
            DiagnosticCheck(
                "Session storage", False, "storage must stay outside workspace"
            )
        )
        checks.append(
            DiagnosticCheck(
                "Memory storage", False, "storage must stay outside workspace"
            )
        )
    else:
        storage_ok, storage_detail = _storage_probe(home)
        checks.append(DiagnosticCheck("Session storage", storage_ok, storage_detail))
        memory_ok, memory_detail = _storage_probe(home)
        checks.append(DiagnosticCheck("Memory storage", memory_ok, memory_detail))

    if workspace_ok:
        try:
            registry = SkillRegistry(home, resolved_workspace)
            skills = registry.discover()
            skills_ok = not registry.diagnostics
            skill_detail = f"{len(skills)} available"
            if not skills_ok:
                skill_detail += f"; {len(registry.diagnostics)} warning(s)"
        except Exception:
            skills_ok = False
            skill_detail = "discovery failed"
        checks.append(DiagnosticCheck("Skills", skills_ok, skill_detail))

        manager: PluginManager | None = None
        try:
            manager = PluginManager(home, resolved_workspace, ToolRegistry())
            plugins = manager.discover()
            plugins_ok = not manager.diagnostics
            plugin_detail = f"{len(plugins)} installed"
            if not plugins_ok:
                plugin_detail += f"; {len(manager.diagnostics)} warning(s)"
        except Exception:
            plugins_ok = False
            plugin_detail = "discovery failed"
        finally:
            if manager is not None:
                manager.close()
        checks.append(DiagnosticCheck("Plugins", plugins_ok, plugin_detail))
    else:
        checks.append(DiagnosticCheck("Skills", False, "workspace unavailable"))
        checks.append(DiagnosticCheck("Plugins", False, "workspace unavailable"))
    return tuple(checks)


def render_doctor(checks: tuple[DiagnosticCheck, ...]) -> str:
    """Render concise diagnostics without accessing configuration values."""

    if type(checks) is not tuple or any(
        not isinstance(item, DiagnosticCheck) for item in checks
    ):
        raise TypeError("checks must be a DiagnosticCheck tuple")
    lines = [
        f"[{'ok' if item.ok else 'fail'}] {item.name}: {item.detail}"
        for item in checks
    ]
    lines.extend(("", "Ready" if checks and all(item.ok for item in checks) else "Not ready"))
    return "\n".join(lines)


def _storage_probe(root: Path) -> tuple[bool, str]:
    path: Path | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix=".doctor-", dir=root)
        os.close(descriptor)
        path = Path(raw_path)
        path.unlink()
        return True, f"writable: {root}"
    except OSError:
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
        return False, f"not writable: {root}"

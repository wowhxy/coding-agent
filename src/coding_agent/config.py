"""Runtime configuration resolution without exposing secret CLI values."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required runtime configuration is absent or invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Resolved immutable configuration for one agent invocation."""

    workspace: Path
    base_url: str
    model: str
    api_key: str = field(repr=False)
    api_key_env: str
    sensitive_env_names: frozenset[str]
    max_steps: int
    max_context_chars: int
    recent_turns: int
    max_tool_output_chars: int
    command_timeout: int


def resolve_config(
    *,
    workspace: str | Path,
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    max_steps: int = 20,
    max_context_chars: int = 80_000,
    recent_turns: int = 8,
    max_tool_output_chars: int = 20_000,
    command_timeout: int = 30,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    """Resolve explicit and environment configuration with fixed precedence."""

    environment = os.environ if environ is None else environ
    resolved_workspace = _resolve_workspace(workspace)
    resolved_base_url = _required_string(
        explicit=base_url,
        environment=environment,
        environment_name="CODING_AGENT_BASE_URL",
        label="base URL",
    )
    resolved_model = _required_string(
        explicit=model,
        environment=environment,
        environment_name="CODING_AGENT_MODEL",
        label="model",
    )

    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ConfigError("api_key_env must be a non-empty string")
    resolved_api_key_env = api_key_env.strip()
    api_key = environment.get(resolved_api_key_env)
    if not isinstance(api_key, str) or not api_key:
        raise ConfigError(
            "missing API key value in environment variable "
            f"{resolved_api_key_env}"
        )

    limits = {
        "max_steps": max_steps,
        "max_context_chars": max_context_chars,
        "recent_turns": recent_turns,
        "max_tool_output_chars": max_tool_output_chars,
        "command_timeout": command_timeout,
    }
    for name, value in limits.items():
        if not _is_positive_int(value):
            raise ConfigError(f"{name} must be a positive integer")
    if command_timeout > 120:
        raise ConfigError("command_timeout must be no greater than 120")

    sensitive_names = {
        name.strip()
        for name in environment.get(
            "CODING_AGENT_SENSITIVE_ENV_NAMES",
            "",
        ).split(",")
        if name.strip()
    }
    sensitive_names.add(resolved_api_key_env)

    return RuntimeConfig(
        workspace=resolved_workspace,
        base_url=resolved_base_url,
        model=resolved_model,
        api_key=api_key,
        api_key_env=resolved_api_key_env,
        sensitive_env_names=frozenset(sensitive_names),
        max_steps=max_steps,
        max_context_chars=max_context_chars,
        recent_turns=recent_turns,
        max_tool_output_chars=max_tool_output_chars,
        command_timeout=command_timeout,
    )


def _resolve_workspace(workspace: str | Path) -> Path:
    try:
        resolved = Path(workspace).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise ConfigError(f"workspace does not exist: {workspace}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"workspace is not a directory: {workspace}")
    return resolved


def _required_string(
    *,
    explicit: str | None,
    environment: Mapping[str, str],
    environment_name: str,
    label: str,
) -> str:
    value = explicit if explicit is not None else environment.get(environment_name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"missing {label}; provide it explicitly or set {environment_name}"
        )
    return value.strip()


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

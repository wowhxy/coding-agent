from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from coding_agent.config import ConfigError, resolve_config
from coding_agent.system_prompt import SYSTEM_PROMPT


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        "CODING_AGENT_BASE_URL": "https://environment.test/v1",
        "CODING_AGENT_MODEL": "environment-model",
        "OPENAI_API_KEY": "fake-config-key",
    }
    environment.update(overrides)
    return environment


def test_resolve_config_applies_documented_defaults(tmp_path: Path) -> None:
    config = resolve_config(workspace=tmp_path, environ=_environment())

    assert config.workspace == tmp_path.resolve()
    assert config.base_url == "https://environment.test/v1"
    assert config.model == "environment-model"
    assert config.api_key == "fake-config-key"
    assert config.api_key_env == "OPENAI_API_KEY"
    assert config.thinking_mode == "provider-default"
    assert config.sensitive_env_names == frozenset({"OPENAI_API_KEY"})
    assert config.max_steps == 20
    assert config.max_context_chars == 80_000
    assert config.recent_turns == 8
    assert config.max_tool_output_chars == 20_000
    assert config.command_timeout == 30


def test_resolve_config_prefers_explicit_base_url_and_model(
    tmp_path: Path,
) -> None:
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://explicit.test/api",
        model="explicit-model",
        environ=_environment(),
    )

    assert config.base_url == "https://explicit.test/api"
    assert config.model == "explicit-model"


def test_resolve_config_supports_deepseek_non_thinking_mode(
    tmp_path: Path,
) -> None:
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        thinking_mode="disabled",
        environ={"DEEPSEEK_API_KEY": "fake-deepseek-key"},
    )

    assert config.thinking_mode == "disabled"
    assert config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.sensitive_env_names == frozenset({"DEEPSEEK_API_KEY"})


def test_resolve_config_rejects_unknown_thinking_mode(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="thinking_mode"):
        resolve_config(
            workspace=tmp_path,
            thinking_mode="enabled",
            environ=_environment(),
        )


@pytest.mark.parametrize(
    ("environment", "missing_name"),
    [
        (
            {
                "CODING_AGENT_MODEL": "model",
                "OPENAI_API_KEY": "fake-key",
            },
            "base URL",
        ),
        (
            {
                "CODING_AGENT_BASE_URL": "https://example.test/v1",
                "OPENAI_API_KEY": "fake-key",
            },
            "model",
        ),
        (
            {
                "CODING_AGENT_BASE_URL": "https://example.test/v1",
                "CODING_AGENT_MODEL": "model",
            },
            "OPENAI_API_KEY",
        ),
    ],
)
def test_resolve_config_rejects_missing_required_values(
    tmp_path: Path,
    environment: dict[str, str],
    missing_name: str,
) -> None:
    with pytest.raises(ConfigError, match=missing_name):
        resolve_config(workspace=tmp_path, environ=environment)


@pytest.mark.parametrize("workspace_kind", ["missing", "file"])
def test_resolve_config_requires_an_existing_workspace_directory(
    tmp_path: Path,
    workspace_kind: str,
) -> None:
    workspace = tmp_path / workspace_kind
    if workspace_kind == "file":
        workspace.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ConfigError, match="workspace"):
        resolve_config(workspace=workspace, environ=_environment())


@pytest.mark.parametrize("max_steps", [0, -1, True, 1.5])
def test_resolve_config_requires_positive_integer_max_steps(
    tmp_path: Path,
    max_steps: object,
) -> None:
    with pytest.raises(ConfigError, match="max_steps"):
        resolve_config(
            workspace=tmp_path,
            max_steps=max_steps,  # type: ignore[arg-type]
            environ=_environment(),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_context_chars", 0),
        ("recent_turns", 0),
        ("max_tool_output_chars", 0),
        ("command_timeout", 0),
        ("command_timeout", 121),
    ],
)
def test_resolve_config_validates_other_runtime_limits(
    tmp_path: Path,
    field_name: str,
    value: int,
) -> None:
    arguments = {field_name: value}

    with pytest.raises(ConfigError, match=field_name):
        resolve_config(
            workspace=tmp_path,
            environ=_environment(),
            **arguments,  # type: ignore[arg-type]
        )


def test_resolve_config_parses_sensitive_names_and_selected_key_name(
    tmp_path: Path,
) -> None:
    environment = _environment(
        CUSTOM_PROVIDER_KEY="fake-custom-key",
        CODING_AGENT_SENSITIVE_ENV_NAMES=(
            " EXTRA_SECRET_A,EXTRA_SECRET_B, ,EXTRA_SECRET_A "
        ),
    )

    config = resolve_config(
        workspace=tmp_path,
        api_key_env="CUSTOM_PROVIDER_KEY",
        environ=environment,
    )

    assert config.api_key == "fake-custom-key"
    assert config.api_key_env == "CUSTOM_PROVIDER_KEY"
    assert config.sensitive_env_names == frozenset(
        {"CUSTOM_PROVIDER_KEY", "EXTRA_SECRET_A", "EXTRA_SECRET_B"}
    )


def test_runtime_config_is_immutable_and_hides_api_key_from_repr(
    tmp_path: Path,
) -> None:
    config = resolve_config(workspace=tmp_path, environ=_environment())

    assert "fake-config-key" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.max_steps = 99  # type: ignore[misc]


def test_system_prompt_contains_minimal_coding_policy() -> None:
    prompt = SYSTEM_PROMPT.lower()

    assert prompt.index("inspect") < prompt.index("edit")
    assert "only the supplied" in prompt
    assert "do not invent" in prompt
    assert "minimal" in prompt
    assert "tool error" in prompt
    assert "validation" in prompt
    assert "verified" in prompt
    assert "unverified" in prompt
    assert "credential" in prompt


def test_system_prompt_separates_protocol_end_from_semantic_correctness() -> None:
    prompt = SYSTEM_PROMPT.lower()

    assert "final response" in prompt
    assert "not proof" in prompt
    assert "tests do not exist or cannot run" in prompt
    assert "report that limitation" in prompt


def test_system_prompt_requires_latest_user_message_language_by_default() -> None:
    assert (
        "Unless the user explicitly requests another language, respond in the "
        "language of the latest user message."
    ) in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "excluded_scope",
    ["action journal", "memory", "other agents", "hosted execution"],
)
def test_system_prompt_does_not_introduce_excluded_scope(
    excluded_scope: str,
) -> None:
    assert excluded_scope not in SYSTEM_PROMPT.lower()

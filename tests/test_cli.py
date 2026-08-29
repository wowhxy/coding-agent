from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.cli import main
from coding_agent.config import resolve_config
from coding_agent.model import ModelClient, ModelTransportError
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall
from coding_agent.session import SessionError
from coding_agent.session_store import JsonSessionStore
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from tests.fakes import FakeModelClient


FAKE_API_KEY = "fake-cli-environment-key"
NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


class _ClosableFakeModelClient(FakeModelClient):
    def __init__(self, script: list[ModelTurn | Exception]) -> None:
        super().__init__(script)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _ClientFactory:
    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str, str, str]] = []

    def __call__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        thinking_mode: str,
    ) -> ModelClient:
        self.calls.append((base_url, model, api_key, thinking_mode))
        return self.client


class _InputReader:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._values)


class _StoreFactory:
    def __init__(self, *session_ids: str) -> None:
        self._session_ids = iter(session_ids)
        self.roots: list[Path] = []

    def __call__(self, root: Path) -> JsonSessionStore:
        self.roots.append(root)
        return JsonSessionStore(
            root,
            clock=lambda: NOW,
            id_generator=lambda: next(self._session_ids),
        )


def _arguments(tmp_path: Path, task: str = "inspect the project") -> list[str]:
    return [
        "--workspace",
        str(tmp_path),
        "--base-url",
        "https://example.test/v1",
        "--model",
        "test-model",
        task,
    ]


def _environment() -> dict[str, str]:
    return {"OPENAI_API_KEY": FAKE_API_KEY}


def _interactive_arguments(
    tmp_path: Path,
    *session_arguments: str,
) -> list[str]:
    return [
        "--workspace",
        str(tmp_path),
        "--base-url",
        "https://example.test/v1",
        "--model",
        "test-model",
        *session_arguments,
    ]


def test_build_default_registry_registers_exactly_six_tools_in_order(
    tmp_path: Path,
) -> None:
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="test-model",
        command_timeout=17,
        environ=_environment(),
    )

    registry = build_default_registry(config)

    assert [definition.name for definition in registry.definitions()] == [
        "list_files",
        "search_text",
        "read_file",
        "write_file",
        "replace_in_file",
        "execute_command",
    ]
    command_schema = registry.definitions()[-1].input_schema
    assert command_schema["properties"]["timeout_seconds"]["default"] == 17


def test_main_accepts_documented_invocation_and_prints_protocol_final(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _ClosableFakeModelClient(
        [ModelTurn(final_text="review complete")]
    )
    factory = _ClientFactory(client)

    exit_code = main(
        _arguments(tmp_path, "review this project"),
        environ=_environment(),
        client_factory=factory,
        session_store_factory=lambda _root: pytest.fail(
            "one-shot mode must not construct a session store"
        ),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[final] protocol status: FINAL_RESPONSE" in captured.out
    assert "[response]\nreview complete" in captured.out
    assert "task succeeded" not in captured.out.lower()
    assert captured.err == ""
    assert factory.calls == [
        (
            "https://example.test/v1",
            "test-model",
            FAKE_API_KEY,
            "provider-default",
        )
    ]
    assert client.close_count == 1
    assert FAKE_API_KEY not in captured.out


def test_main_uses_current_directory_and_deepseek_provider_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    deepseek_key = "fake-deepseek-preset-key"
    factory = _ClientFactory(FakeModelClient([ModelTurn(final_text="done")]))

    exit_code = main(
        ["--provider", "deepseek", "inspect this project"],
        environ={"DEEPSEEK_API_KEY": deepseek_key},
        client_factory=factory,
        secret_reader=lambda _: pytest.fail("key prompt was not expected"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert factory.calls == [
        (
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            deepseek_key,
            "disabled",
        )
    ]
    assert f"[run] workspace: {tmp_path.resolve()}" in captured.out
    assert "[run] provider: deepseek; model: deepseek-v4-flash" in captured.out
    assert deepseek_key not in captured.out
    assert deepseek_key not in captured.err


def test_main_securely_prompts_for_missing_deepseek_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompted_key = "fake-prompted-deepseek-key"
    prompts: list[str] = []
    factory = _ClientFactory(FakeModelClient([ModelTurn(final_text="done")]))

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return prompted_key

    exit_code = main(
        [
            "--provider",
            "deepseek",
            "--workspace",
            str(tmp_path),
            "inspect this project",
        ],
        environ={},
        client_factory=factory,
        secret_reader=read_secret,
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert prompts == ["DeepSeek API Key (input hidden): "]
    assert factory.calls[0][2] == prompted_key
    assert prompted_key not in captured.out
    assert prompted_key not in captured.err


def test_main_uses_openai_provider_preset_with_explicit_model(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeModelClient([ModelTurn(final_text="done")])
    factory = _ClientFactory(client)

    exit_code = main(
        [
            "--provider",
            "openai",
            "--workspace",
            str(tmp_path),
            "--model",
            "openai-test-model",
            "inspect this project",
        ],
        environ={"OPENAI_API_KEY": FAKE_API_KEY},
        client_factory=factory,
        secret_reader=lambda _: pytest.fail("key prompt was not expected"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert factory.calls == [
        (
            "https://api.openai.com/v1",
            "openai-test-model",
            FAKE_API_KEY,
            "provider-default",
        )
    ]
    assert "[run] provider: openai; model: openai-test-model" in captured.out
    assert FAKE_API_KEY not in captured.out


def test_help_describes_optional_interactive_task_and_session_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--help"])
    captured = capsys.readouterr()
    normalized_help = " ".join(captured.out.split())

    assert exit_code == 0
    assert "[task]" in normalized_help
    assert "interactive" in normalized_help.lower()
    assert "--new-session" in normalized_help
    assert "--resume-session SESSION_ID" in normalized_help


def test_session_selection_flags_are_mutually_exclusive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        _interactive_arguments(
            tmp_path,
            "--new-session",
            "--resume-session",
            "012345abcdef",
        ),
        environ=_environment(),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "not allowed with argument" in captured.err


@pytest.mark.parametrize(
    "session_arguments",
    [
        ["--new-session"],
        ["--resume-session", "012345abcdef"],
    ],
)
def test_session_flags_with_one_shot_task_fail_before_any_construction_or_prompt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    session_arguments: list[str],
) -> None:
    factory = _ClientFactory(FakeModelClient([]))
    store_roots: list[Path] = []

    exit_code = main(
        _interactive_arguments(tmp_path, *session_arguments, "task"),
        environ={},
        client_factory=factory,
        secret_reader=lambda _prompt: pytest.fail("key prompt was not expected"),
        session_store_factory=store_roots.append,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "only valid in interactive mode" in captured.err
    assert factory.calls == []
    assert store_roots == []


def test_help_explains_that_thinking_mode_comes_from_provider_preset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--help"])
    captured = capsys.readouterr()
    normalized_help = " ".join(captured.out.split())

    assert exit_code == 0
    assert "default: provider preset" in normalized_help
    assert "default: provider-default" not in normalized_help


def test_main_does_not_accept_a_raw_api_key_argument(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ClientFactory(FakeModelClient([]))
    arguments = _arguments(tmp_path)[:-1] + [
        "--api-key",
        "literal-key-value",
        "task",
    ]

    exit_code = main(
        arguments,
        environ=_environment(),
        client_factory=factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "raw api-key arguments are not supported" in captured.err.lower()
    assert "literal-key-value" not in captured.err
    assert factory.calls == []


def test_main_rejects_raw_api_key_equals_form_without_echoing_value(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ClientFactory(FakeModelClient([]))
    arguments = _arguments(tmp_path)[:-1] + [
        "--api-key=literal-key-value",
        "task",
    ]

    exit_code = main(
        arguments,
        environ=_environment(),
        client_factory=factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "raw api-key arguments are not supported" in captured.err.lower()
    assert "literal-key-value" not in captured.err
    assert factory.calls == []


@pytest.mark.parametrize(
    ("key_env_name", "reserved_name"),
    [
        ("CODING_AGENT_BASE_URL", "CODING_AGENT_BASE_URL"),
        ("CODING_AGENT_MODEL", "CODING_AGENT_MODEL"),
        (
            "CODING_AGENT_SENSITIVE_ENV_NAMES",
            "CODING_AGENT_SENSITIVE_ENV_NAMES",
        ),
        ("CODING_AGENT_HOME", "CODING_AGENT_HOME"),
        ("LOCALAPPDATA", "LOCALAPPDATA"),
        ("XDG_DATA_HOME", "XDG_DATA_HOME"),
        ("coding_agent_home", "CODING_AGENT_HOME"),
    ],
)
def test_reserved_api_key_env_is_rejected_before_prompt_or_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    key_env_name: str,
    reserved_name: str,
) -> None:
    prompted: list[str] = []
    store_roots: list[Path] = []
    client_factory = _ClientFactory(FakeModelClient([]))
    secret = "synthetic-reserved-name-secret"
    monkeypatch.setattr(
        "coding_agent.cli.resolve_config",
        lambda **_arguments: pytest.fail(
            "runtime config resolution was not expected"
        ),
    )

    exit_code = main(
        [
            "--workspace",
            str(tmp_path),
            "--base-url",
            "https://example.test/v1",
            "--model",
            "test-model",
            "--api-key-env",
            key_env_name,
        ],
        environ={},
        client_factory=client_factory,
        secret_reader=lambda prompt: prompted.append(prompt) or secret,
        session_store_factory=lambda root: store_roots.append(root),
        input_reader=lambda _prompt: pytest.fail("input was not expected"),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "[error]" in captured.err
    assert reserved_name in captured.err
    assert secret not in captured.err
    assert prompted == []
    assert store_roots == []
    assert client_factory.calls == []


def test_model_api_key_env_is_rejected_before_secret_becomes_model_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "synthetic-model-secret"
    prompted: list[str] = []
    client_factory = _ClientFactory(
        FakeModelClient([ModelTurn(final_text="must not run")])
    )

    exit_code = main(
        [
            "--workspace",
            str(tmp_path),
            "--base-url",
            "https://example.test/v1",
            "--api-key-env",
            "CODING_AGENT_MODEL",
            "task",
        ],
        environ={},
        client_factory=client_factory,
        secret_reader=lambda prompt: prompted.append(prompt) or secret,
        session_store_factory=lambda _root: pytest.fail(
            "session store was not expected"
        ),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "CODING_AGENT_MODEL" in captured.err
    assert secret not in captured.err
    assert prompted == []
    assert client_factory.calls == []


def test_arbitrary_custom_api_key_env_remains_supported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    custom_key = "synthetic-custom-provider-secret"
    client = _ClosableFakeModelClient([ModelTurn(final_text="done")])
    factory = _ClientFactory(client)

    exit_code = main(
        _arguments(tmp_path)[:-1]
        + ["--api-key-env", "MY_PROVIDER_SECRET", "task"],
        environ={"MY_PROVIDER_SECRET": custom_key},
        client_factory=factory,
        secret_reader=lambda _prompt: pytest.fail(
            "key prompt was not expected"
        ),
        session_store_factory=lambda _root: pytest.fail(
            "one-shot mode must not construct a session store"
        ),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert factory.calls[0][2] == custom_key
    assert client.close_count == 1
    assert custom_key not in captured.out
    assert custom_key not in captured.err


def test_main_returns_two_for_invalid_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ClientFactory(FakeModelClient([]))

    exit_code = main(
        _arguments(tmp_path),
        environ={},
        client_factory=factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "[error]" in captured.err
    assert "OPENAI_API_KEY" in captured.err
    assert "Traceback" not in captured.err
    assert factory.calls == []


def test_main_prints_tool_request_and_result_events_in_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("list-1", "list_files", '{"path":"."}'),
                )
            ),
            ModelTurn(final_text="inspection finished"),
        ]
    )

    exit_code = main(
        _arguments(tmp_path),
        environ=_environment(),
        client_factory=_ClientFactory(client),
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    request_position = output.index(
        "[step 1] model requested: list_files"
    )
    result_position = output.index("[tool] list_files: ok")
    final_position = output.index(
        "[final] protocol status: FINAL_RESPONSE"
    )
    response_position = output.index("[response]\ninspection finished")
    assert request_position < result_position < final_position < response_position


def _status_case(status: str) -> tuple[list[ModelTurn | Exception], list[str]]:
    if status == "MAX_STEPS":
        return (
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall("one", "list_files", '{"path":"."}'),
                    )
                )
            ],
            ["--max-steps", "1"],
        )
    if status == "STALLED":
        repeated = ModelTurn(
            tool_calls=(ToolCall("same", "missing_tool", "{}"),)
        )
        return ([repeated, repeated, repeated], [])
    if status == "MODEL_ERROR":
        return (
            [ModelTransportError(f"provider rejected {FAKE_API_KEY}")],
            [],
        )
    if status == "INTERNAL_ERROR":
        return ([RuntimeError(f"unexpected {FAKE_API_KEY}")], [])
    raise AssertionError(f"unknown test status: {status}")


@pytest.mark.parametrize(
    ("status", "expected_exit_code"),
    [
        ("MAX_STEPS", 3),
        ("STALLED", 4),
        ("MODEL_ERROR", 5),
        ("INTERNAL_ERROR", 6),
    ],
)
def test_main_maps_nonfinal_statuses_and_redacts_environment_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_exit_code: int,
) -> None:
    script, extra_arguments = _status_case(status)
    arguments = _arguments(tmp_path)[:-1] + extra_arguments + ["task"]

    exit_code = main(
        arguments,
        environ=_environment(),
        client_factory=_ClientFactory(FakeModelClient(script)),
    )
    captured = capsys.readouterr()

    assert exit_code == expected_exit_code
    assert f"[final] protocol status: {status}" in captured.out
    assert "[error]" in captured.err
    assert FAKE_API_KEY not in captured.out
    assert FAKE_API_KEY not in captured.err


class _ExplodingFactory:
    def __call__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        thinking_mode: str,
    ) -> ModelClient:
        raise RuntimeError(f"constructor exposed {api_key}")


def test_main_composes_deepseek_non_thinking_client_without_leaking_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deepseek_key = "fake-deepseek-cli-key"
    factory = _ClientFactory(FakeModelClient([ModelTurn(final_text="done")]))

    exit_code = main(
        [
            "--workspace",
            str(tmp_path),
            "--base-url",
            "https://api.deepseek.com",
            "--model",
            "deepseek-v4-flash",
            "--api-key-env",
            "DEEPSEEK_API_KEY",
            "--thinking-mode",
            "disabled",
            "inspect this project",
        ],
        environ={"DEEPSEEK_API_KEY": deepseek_key},
        client_factory=factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert factory.calls == [
        (
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            deepseek_key,
            "disabled",
        )
    ]
    assert deepseek_key not in captured.out
    assert deepseek_key not in captured.err


def test_main_converts_composition_failure_to_concise_internal_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        _arguments(tmp_path),
        environ=_environment(),
        client_factory=_ExplodingFactory(),
    )
    captured = capsys.readouterr()

    assert exit_code == 6
    assert captured.out == ""
    assert "[error] unexpected internal error: RuntimeError" in captured.err
    assert "Traceback" not in captured.err
    assert FAKE_API_KEY not in captured.err


def test_main_passes_context_and_tool_definitions_to_fake_model(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeModelClient([ModelTurn(final_text="done")])

    exit_code = main(
        _arguments(tmp_path, "original task text"),
        environ=_environment(),
        client_factory=_ClientFactory(client),
    )
    capsys.readouterr()

    assert exit_code == 0
    messages, definitions = client.calls[0]
    assert [message.content for message in messages] == [
        SYSTEM_PROMPT,
        "original task text",
    ]
    assert [definition.name for definition in definitions] == [
        "list_files",
        "search_text",
        "read_file",
        "write_file",
        "replace_in_file",
        "execute_command",
        "delegate_tasks",
    ]


def test_interactive_default_creates_turns_and_closes_one_reused_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    root = tmp_path / "session-home"
    store_factory = _StoreFactory("012345abcdef")
    client = _ClosableFakeModelClient(
        [ModelTurn("first answer"), ModelTurn("second answer")]
    )
    client_factory = _ClientFactory(client)
    inputs = _InputReader("first task", "follow-up", "/exit")
    secret_prompts: list[str] = []

    exit_code = main(
        [
            "--base-url",
            "https://example.test/v1",
            "--model",
            "test-model",
        ],
        environ={"CODING_AGENT_HOME": str(root)},
        client_factory=client_factory,
        secret_reader=lambda prompt: (
            secret_prompts.append(prompt) or FAKE_API_KEY
        ),
        session_store_factory=store_factory,
        input_reader=inputs,
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert secret_prompts == ["Provider API Key (input hidden): "]
    assert client_factory.calls == [
        (
            "https://example.test/v1",
            "test-model",
            FAKE_API_KEY,
            "provider-default",
        )
    ]
    assert len(client.calls) == 2
    assert all(
        [definition.name for definition in definitions]
        == [
            "list_files",
            "search_text",
            "read_file",
            "write_file",
            "replace_in_file",
            "execute_command",
            "delegate_tasks",
        ]
        for _messages, definitions in client.calls
    )
    assert client.close_count == 1
    assert store_factory.roots == [root]
    assert f"[run] workspace: {workspace.resolve()}" in captured.out
    assert "[run] provider: custom; model: test-model" in captured.out
    assert "[session] created: 012345abcdef" in captured.out
    assert "[session] enter /exit or press Ctrl+C to save and exit" in captured.out
    assert captured.out.count("[final] protocol status: FINAL_RESPONSE") == 2
    assert "agent> first answer" in captured.out
    assert "agent> second answer" in captured.out
    assert "[response]" not in captured.out
    assert captured.err == ""
    assert JsonSessionStore(root).load_latest(workspace).session_id == "012345abcdef"
    assert FAKE_API_KEY not in captured.out
    assert FAKE_API_KEY not in captured.err


def test_interactive_default_later_invocation_resumes_latest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "session-home"
    store_factory = _StoreFactory("111111111111")
    first_client = _ClosableFakeModelClient([ModelTurn("saved answer")])

    assert main(
        _interactive_arguments(workspace),
        environ={**_environment(), "CODING_AGENT_HOME": str(root)},
        client_factory=_ClientFactory(first_client),
        session_store_factory=store_factory,
        input_reader=_InputReader("persist me", "/exit"),
    ) == 0
    capsys.readouterr()

    second_client = _ClosableFakeModelClient([])
    exit_code = main(
        _interactive_arguments(workspace),
        environ={**_environment(), "CODING_AGENT_HOME": str(root)},
        client_factory=_ClientFactory(second_client),
        session_store_factory=store_factory,
        input_reader=_InputReader("/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[session] resumed: 111111111111" in captured.out
    assert second_client.calls == []
    assert second_client.close_count == 1


def test_interactive_new_session_keeps_existing_persisted_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "session-home"
    store_factory = _StoreFactory("111111111111", "222222222222")
    first_client = _ClosableFakeModelClient([ModelTurn("saved answer")])

    assert main(
        _interactive_arguments(workspace),
        environ={**_environment(), "CODING_AGENT_HOME": str(root)},
        client_factory=_ClientFactory(first_client),
        session_store_factory=store_factory,
        input_reader=_InputReader("persist me", "/exit"),
    ) == 0
    capsys.readouterr()

    new_client = _ClosableFakeModelClient([])
    exit_code = main(
        _interactive_arguments(workspace, "--new-session"),
        environ={**_environment(), "CODING_AGENT_HOME": str(root)},
        client_factory=_ClientFactory(new_client),
        session_store_factory=store_factory,
        input_reader=_InputReader("/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[session] created: 222222222222" in captured.out
    assert JsonSessionStore(root).load_latest(workspace).session_id == "111111111111"
    assert (root / "sessions" / "111111111111.json").is_file()
    assert not (root / "sessions" / "222222222222.json").exists()
    assert new_client.close_count == 1


def test_explicit_resume_restores_older_history_under_current_system_and_updates_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "session-home"
    identifiers = iter(("111111111111", "222222222222"))
    store = JsonSessionStore(
        root,
        clock=lambda: NOW,
        id_generator=lambda: next(identifiers),
    )
    older = store.create_session(workspace, "old-provider", "old-model")
    newer = store.create_session(workspace, "other-provider", "other-model")
    store.save(
        replace(
            older,
            messages=(
                Message(Role.USER, "older question"),
                Message(Role.ASSISTANT, "older answer"),
            ),
        )
    )
    store.save(
        replace(newer, messages=(Message(Role.USER, "newer question"),))
    )
    client = _ClosableFakeModelClient([ModelTurn("current answer")])

    exit_code = main(
        _interactive_arguments(
            workspace,
            "--resume-session",
            "111111111111",
        ),
        environ={**_environment(), "CODING_AGENT_HOME": str(root)},
        client_factory=_ClientFactory(client),
        session_store_factory=lambda _root: store,
        input_reader=_InputReader("current question", "/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[session] resumed: 111111111111" in captured.out
    assert "[warning] session provider changed: old-provider -> custom" in captured.err
    assert "[warning] session model changed: old-model -> test-model" in captured.err
    messages, _definitions = client.calls[0]
    assert messages == (
        Message(Role.SYSTEM, SYSTEM_PROMPT),
        Message(Role.USER, "older question"),
        Message(Role.ASSISTANT, "older answer"),
        Message(Role.USER, "current question"),
    )
    assert sum(message.role is Role.SYSTEM for message in messages) == 1
    saved = store.load_session("111111111111", workspace)
    assert (saved.provider, saved.model) == ("custom", "test-model")
    assert saved.messages[-2:] == (
        Message(Role.USER, "current question"),
        Message(Role.ASSISTANT, "current answer"),
    )
    assert client.close_count == 1


class _FailingSelectionStore:
    def __init__(self, code: str, message: str) -> None:
        self.error = SessionError(code, message)

    def create_session(self, *_arguments: object) -> object:
        raise self.error

    def load_latest(self, *_arguments: object) -> object:
        raise self.error

    def load_session(self, *_arguments: object) -> object:
        raise self.error


@pytest.mark.parametrize(
    ("label", "session_arguments", "code"),
    [
        (
            "wrong workspace",
            ["--resume-session", "111111111111"],
            "SESSION_WORKSPACE_MISMATCH",
        ),
        (
            "unknown id",
            ["--resume-session", "111111111111"],
            "SESSION_NOT_FOUND",
        ),
        ("corrupt index", [], "SESSION_INDEX_CORRUPT"),
        (
            "corrupt session",
            ["--resume-session", "111111111111"],
            "SESSION_CORRUPT",
        ),
        (
            "unsupported version",
            ["--resume-session", "111111111111"],
            "SESSION_VERSION_UNSUPPORTED",
        ),
        (
            "new session initialization",
            ["--new-session"],
            "SESSION_SAVE_FAILED",
        ),
        ("store io", [], "SESSION_IO_ERROR"),
    ],
)
def test_interactive_selection_errors_are_concise_and_do_not_construct_client(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    label: str,
    session_arguments: list[str],
    code: str,
) -> None:
    client_factory = _ClientFactory(FakeModelClient([]))
    message = f"{label} containing {FAKE_API_KEY}"

    exit_code = main(
        _interactive_arguments(tmp_path, *session_arguments),
        environ={
            **_environment(),
            "CODING_AGENT_HOME": str(tmp_path / "session-home"),
        },
        client_factory=client_factory,
        session_store_factory=lambda _root: _FailingSelectionStore(
            code,
            message,
        ),
        input_reader=lambda _prompt: pytest.fail("input was not expected"),
    )
    captured = capsys.readouterr()

    assert exit_code == 7
    assert captured.out == ""
    assert f"[error] {code}: {label} containing [REDACTED]" in captured.err
    assert "Traceback" not in captured.err
    assert FAKE_API_KEY not in captured.err
    assert client_factory.calls == []


def test_interactive_rejects_session_home_inside_workspace_before_client_or_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_home = workspace / "session-data"
    client_factory = _ClientFactory(_ClosableFakeModelClient([]))

    exit_code = main(
        _interactive_arguments(workspace),
        environ={**_environment(), "CODING_AGENT_HOME": str(session_home)},
        client_factory=client_factory,
        input_reader=_InputReader("/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 7
    assert captured.out == ""
    assert captured.err == (
        "[error] SESSION_IO_ERROR: "
        "session storage root must be outside workspace\n"
    )
    assert client_factory.calls == []
    assert not session_home.exists()


@pytest.mark.parametrize(
    ("script", "extra_arguments", "status"),
    [
        (
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall("one", "list_files", '{"path":"."}'),
                    )
                )
            ],
            ["--max-steps", "1"],
            "MAX_STEPS",
        ),
        ([ModelTransportError("provider failed")], [], "MODEL_ERROR"),
        ([RuntimeError("runner failed")], [], "INTERNAL_ERROR"),
    ],
)
def test_interactive_protocol_statuses_render_and_close_client_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    script: list[ModelTurn | Exception],
    extra_arguments: list[str],
    status: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = _ClosableFakeModelClient(script)

    exit_code = main(
        _interactive_arguments(workspace, *extra_arguments),
        environ={
            **_environment(),
            "CODING_AGENT_HOME": str(tmp_path / f"session-home-{status}"),
        },
        client_factory=_ClientFactory(client),
        session_store_factory=_StoreFactory("abcdefabcdef"),
        input_reader=_InputReader("task", "/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"[final] protocol status: {status}" in captured.out
    assert client.close_count == 1
    assert FAKE_API_KEY not in captured.out
    assert FAKE_API_KEY not in captured.err


class _SaveFailingStore:
    def __init__(self, root: Path) -> None:
        self._store = JsonSessionStore(
            root,
            clock=lambda: NOW,
            id_generator=lambda: "abcdefabcdef",
        )

    def create_session(self, *arguments: object) -> object:
        return self._store.create_session(*arguments)  # type: ignore[arg-type]

    def load_latest(self, *arguments: object) -> object:
        return self._store.load_latest(*arguments)  # type: ignore[arg-type]

    def load_session(self, *arguments: object) -> object:
        return self._store.load_session(*arguments)  # type: ignore[arg-type]

    def save(self, _record: object) -> object:
        raise SessionError(
            "SESSION_SAVE_FAILED",
            f"could not save {FAKE_API_KEY}",
        )


def test_interactive_save_failure_returns_seven_and_closes_client_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = _ClosableFakeModelClient([ModelTurn(f"answer {FAKE_API_KEY}")])

    exit_code = main(
        _interactive_arguments(workspace),
        environ={
            **_environment(),
            "CODING_AGENT_HOME": str(tmp_path / "session-home"),
        },
        client_factory=_ClientFactory(client),
        session_store_factory=_SaveFailingStore,
        input_reader=_InputReader("task", "/exit"),
    )
    captured = capsys.readouterr()

    assert exit_code == 7
    assert "agent> answer [REDACTED]" in captured.out
    assert "[error] SESSION_SAVE_FAILED: could not save [REDACTED]" in captured.err
    assert client.close_count == 1
    assert FAKE_API_KEY not in captured.out
    assert FAKE_API_KEY not in captured.err

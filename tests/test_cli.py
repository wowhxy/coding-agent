from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.cli import main
from coding_agent.config import resolve_config
from coding_agent.model import ModelClient, ModelTransportError
from coding_agent.protocol import ModelTurn, ToolCall
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from tests.fakes import FakeModelClient


FAKE_API_KEY = "fake-cli-environment-key"


class _ClosableFakeModelClient(FakeModelClient):
    def __init__(self, script: list[ModelTurn | Exception]) -> None:
        super().__init__(script)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _ClientFactory:
    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self,
        base_url: str,
        model: str,
        api_key: str,
    ) -> ModelClient:
        self.calls.append((base_url, model, api_key))
        return self.client


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
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[final] protocol status: FINAL_RESPONSE" in captured.out
    assert "[response]\nreview complete" in captured.out
    assert "task succeeded" not in captured.out.lower()
    assert captured.err == ""
    assert factory.calls == [
        ("https://example.test/v1", "test-model", FAKE_API_KEY)
    ]
    assert client.close_count == 1
    assert FAKE_API_KEY not in captured.out


def test_main_requires_a_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ClientFactory(FakeModelClient([]))
    arguments = _arguments(tmp_path)[:-1]

    exit_code = main(
        arguments,
        environ=_environment(),
        client_factory=factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "task" in captured.err.lower()
    assert factory.calls == []


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
    ) -> ModelClient:
        raise RuntimeError(f"constructor exposed {api_key}")


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
    ]

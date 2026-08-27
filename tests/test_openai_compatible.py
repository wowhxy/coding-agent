from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

import coding_agent.providers.openai_compatible as provider_module
from coding_agent.model import ModelProtocolError, ModelTransportError
from coding_agent.protocol import Message, Role, ToolCall, ToolDefinition
from coding_agent.providers.openai_compatible import OpenAICompatibleClient


FAKE_API_KEY = "unit-test-key"
FINAL_PAYLOAD = {
    "choices": [
        {"message": {"role": "assistant", "content": "finished"}}
    ]
}


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> tuple[OpenAICompatibleClient, httpx.Client]:
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleClient(
        "https://example.test/v1/",
        "test-model",
        FAKE_API_KEY,
        http_client=http_client,
        sleep=sleep,
    )
    return adapter, http_client


def test_complete_maps_internal_request_to_openai_compatible_shape() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FINAL_PAYLOAD)

    adapter, http_client = _adapter(handler)
    messages = (
        Message(Role.SYSTEM, "policy"),
        Message(Role.USER, "fix the project"),
        Message(
            Role.ASSISTANT,
            None,
            (
                ToolCall(
                    "call-1",
                    "read_file",
                    '{"path":"sample.py"}',
                ),
            ),
        ),
        Message(
            Role.TOOL,
            '{"ok":true,"output":"content"}',
            tool_call_id="call-1",
        ),
    )
    definition = ToolDefinition(
        "read_file",
        "Read one local file.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    try:
        turn = adapter.complete(messages, (definition,))
    finally:
        http_client.close()

    assert turn.final_text == "finished"
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    payload = json.loads(request.content)
    assert payload == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "fix the project"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"sample.py"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": '{"ok":true,"output":"content"}',
                "tool_call_id": "call-1",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one local file.",
                    "parameters": definition.input_schema,
                },
            }
        ],
        "stream": False,
    }
    serialized = json.dumps(payload)
    assert "code_interpreter" not in serialized
    assert "file_search" not in serialized


def test_complete_parses_final_response() -> None:
    adapter, http_client = _adapter(
        lambda _: httpx.Response(200, json=FINAL_PAYLOAD)
    )

    try:
        turn = adapter.complete((Message(Role.USER, "task"),), ())
    finally:
        http_client.close()

    assert turn.final_text == "finished"
    assert turn.tool_calls == ()


def test_complete_preserves_content_and_raw_tool_arguments() -> None:
    arguments_json = '{ "path": "a.py" }'
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I will inspect the file.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": arguments_json,
                            },
                        }
                    ],
                }
            }
        ]
    }
    adapter, http_client = _adapter(
        lambda _: httpx.Response(200, json=payload)
    )

    try:
        turn = adapter.complete((), ())
    finally:
        http_client.close()

    assert turn.final_text == "I will inspect the file."
    assert turn.tool_calls == (
        ToolCall("call-1", "read_file", arguments_json),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": "wrong"},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": "wrong"}]},
        {
            "choices": [
                {"message": {"role": "assistant", "content": 123}}
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": "wrong",
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "read_file"},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [],
                    }
                }
            ]
        },
    ],
    ids=[
        "missing-choices",
        "choices-wrong-type",
        "choices-empty",
        "missing-message",
        "message-wrong-type",
        "content-wrong-type",
        "tool-calls-wrong-type",
        "missing-call-id",
        "missing-call-name",
        "missing-call-arguments",
        "no-content-or-calls",
    ],
)
def test_complete_rejects_malformed_response_envelopes(
    payload: object,
) -> None:
    adapter, http_client = _adapter(
        lambda _: httpx.Response(200, json=payload)
    )

    try:
        with pytest.raises(ModelProtocolError) as caught:
            adapter.complete((), ())
    finally:
        http_client.close()

    assert FAKE_API_KEY not in str(caught.value)


def test_complete_rejects_malformed_json_without_leaking_key() -> None:
    adapter, http_client = _adapter(
        lambda _: httpx.Response(200, content=b"not-json")
    )

    try:
        with pytest.raises(ModelProtocolError) as caught:
            adapter.complete((), ())
    finally:
        http_client.close()

    assert "JSON" in str(caught.value)
    assert FAKE_API_KEY not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        408,
        429,
        500,
        502,
        503,
        504,
        "connect",
        "timeout",
    ],
)
def test_complete_retries_transient_failures_at_most_three_times(
    failure: int | str,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "connect":
            raise httpx.ConnectError(
                f"cannot connect using {FAKE_API_KEY}",
                request=request,
            )
        if failure == "timeout":
            raise httpx.ReadTimeout(
                f"timed out using {FAKE_API_KEY}",
                request=request,
            )
        return httpx.Response(failure)

    adapter, http_client = _adapter(handler, sleep=sleeps.append)

    try:
        with pytest.raises(ModelTransportError) as caught:
            adapter.complete((), ())
    finally:
        http_client.close()

    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert FAKE_API_KEY not in str(caught.value)


@pytest.mark.parametrize("status_code", [400, 401])
def test_complete_does_not_retry_permanent_client_errors(
    status_code: int,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code)

    adapter, http_client = _adapter(handler, sleep=sleeps.append)

    try:
        with pytest.raises(ModelTransportError) as caught:
            adapter.complete((), ())
    finally:
        http_client.close()

    assert attempts == 1
    assert sleeps == []
    assert str(status_code) in str(caught.value)
    assert FAKE_API_KEY not in str(caught.value)


def test_complete_succeeds_on_third_transient_attempt() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=FINAL_PAYLOAD)

    adapter, http_client = _adapter(handler, sleep=sleeps.append)

    try:
        turn = adapter.complete((), ())
    finally:
        http_client.close()

    assert turn.final_text == "finished"
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


class _OwnedClientSpy:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_close_closes_internally_owned_client_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = _OwnedClientSpy()
    received_kwargs: list[dict[str, object]] = []

    def construct_client(**kwargs: object) -> _OwnedClientSpy:
        received_kwargs.append(kwargs)
        return owned

    monkeypatch.setattr(provider_module.httpx, "Client", construct_client)
    adapter = OpenAICompatibleClient(
        "https://example.test/v1",
        "test-model",
        FAKE_API_KEY,
    )

    adapter.close()
    adapter.close()

    assert received_kwargs == [{"timeout": 30.0}]
    assert owned.close_count == 1


def test_close_does_not_close_injected_client() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=FINAL_PAYLOAD)
        )
    )
    adapter = OpenAICompatibleClient(
        "https://example.test/v1",
        "test-model",
        FAKE_API_KEY,
        http_client=http_client,
    )

    try:
        adapter.close()
        adapter.close()
        assert http_client.is_closed is False
    finally:
        http_client.close()

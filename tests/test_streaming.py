from __future__ import annotations

import json

import httpx
import pytest

from coding_agent.agent import AgentRunner
from coding_agent.cli import _print_interactive_result
from coding_agent.context import ContextManager
from coding_agent.model import ModelProtocolError
from coding_agent.protocol import Message, ModelTurn, Role, RunStatus, ToolCall
from coding_agent.providers.openai_compatible import OpenAICompatibleClient
from coding_agent.tools.registry import ToolRegistry


def _sse(*events: object) -> bytes:
    lines = [
        "data: " + (event if isinstance(event, str) else json.dumps(event)) + "\n\n"
        for event in events
    ]
    return "".join(lines).encode("utf-8")


def _client(content: bytes) -> tuple[OpenAICompatibleClient, httpx.Client, list[dict[str, object]]]:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return (
        OpenAICompatibleClient(
            "https://example.test/v1", "model", "secret", http_client=http_client
        ),
        http_client,
        payloads,
    )


def test_provider_streams_unicode_text_and_returns_normalized_turn() -> None:
    client, http_client, payloads = _client(
        _sse(
            {"choices": [{"delta": {"role": "assistant", "content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]},
            "[DONE]",
        )
    )
    chunks: list[str] = []
    try:
        turn = client.complete_streaming((Message(Role.USER, "task"),), (), chunks.append)
    finally:
        http_client.close()

    assert chunks == ["你", "好"]
    assert turn == ModelTurn("你好")
    assert payloads[0]["stream"] is True


def test_provider_assembles_fragmented_tool_calls_without_streaming_arguments() -> None:
    client, http_client, _ = _client(
        _sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-", "type": "function", "function": {"name": "read_", "arguments": "{\"pa"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "1", "function": {"name": "file", "arguments": "th\":\"a.py\"}"}}]}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        )
    )
    chunks: list[str] = []
    try:
        turn = client.complete_streaming((), (), chunks.append)
    finally:
        http_client.close()

    assert chunks == []
    assert turn.tool_calls == (ToolCall("call-1", "read_file", '{"path":"a.py"}'),)


@pytest.mark.parametrize(
    "content",
    (
        b"data: not-json\n\n",
        _sse({"choices": [{"delta": {"content": 7}}]}, "[DONE]"),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": "bad"}]}}]}, "[DONE]"),
        _sse({"choices": [{"delta": {"content": "unfinished"}}]}),
    ),
)
def test_provider_rejects_malformed_stream(content: bytes) -> None:
    client, http_client, _ = _client(content)
    try:
        with pytest.raises(ModelProtocolError):
            client.complete_streaming((), (), lambda _chunk: None)
    finally:
        http_client.close()


class _StreamingClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = iter(turns)
        self.complete_calls = 0

    def complete(self, messages, tools) -> ModelTurn:
        self.complete_calls += 1
        raise AssertionError("streaming path expected")

    def complete_streaming(self, messages, tools, sink) -> ModelTurn:
        turn = next(self.turns)
        if turn.final_text:
            sink(turn.final_text)
        return turn


def test_agent_runner_uses_optional_streaming_and_marks_final_as_already_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _StreamingClient([ModelTurn("streamed answer")])
    chunks: list[str] = []
    runner = AgentRunner(client, ToolRegistry(), ContextManager(), text_sink=chunks.append)  # type: ignore[arg-type]

    result = runner.run("system", "task")
    _print_interactive_result(result, "")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.streamed is True
    assert chunks == ["streamed answer"]
    assert "agent> streamed answer" not in capsys.readouterr().out
    assert client.complete_calls == 0

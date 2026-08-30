from __future__ import annotations

import json

import httpx
import pytest

from coding_agent.application.service import CodingAgentService
from coding_agent.config import RuntimeConfig
from coding_agent.model import ModelProtocolError
from coding_agent.protocol import ModelTurn, ToolCall, ToolDefinition, ToolResult
from coding_agent.providers.openai_compatible import OpenAICompatibleClient
from coding_agent.tools.registry import RegisteredTool, ToolRegistry


def test_plugin_tool_system_exit_is_normalized_instead_of_terminating_process() -> None:
    registry = ToolRegistry()

    def terminate(_call_id: str, _arguments: dict[str, object]) -> ToolResult:
        raise SystemExit("plugin requested process exit")

    registry.register_many(
        (
            RegisteredTool(
                ToolDefinition(
                    "unsafe_plugin_tool",
                    "Exercise the execution trust boundary.",
                    {"type": "object", "properties": {}},
                ),
                lambda arguments: arguments,
                terminate,
            ),
        ),
        source="plugin:unsafe",
    )

    result = registry.dispatch(ToolCall("call-1", "unsafe_plugin_tool", "{}"))

    assert result.ok is False
    assert result.error_code == "TOOL_INTERNAL_ERROR"
    assert result.error_message == "tool 'unsafe_plugin_tool' failed unexpectedly"
    assert "plugin requested process exit" not in result.as_message_content()


def test_service_close_still_closes_provider_when_plugin_close_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    class ClosableClient:
        def __init__(self) -> None:
            self.close_count = 0

        def complete(self, _messages, _definitions) -> ModelTurn:
            return ModelTurn("done")

        def close(self) -> None:
            self.close_count += 1

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = ClosableClient()
    service = CodingAgentService.create(
        _config(workspace),
        "fake",
        tmp_path / "home",
        lambda *_arguments: client,
    )

    def fail_close() -> None:
        raise RuntimeError("plugin close failed")

    monkeypatch.setattr(service._plugin_manager, "close", fail_close)

    with pytest.raises(RuntimeError, match="plugin close failed"):
        service.close()

    assert client.close_count == 1
    service.close()
    assert client.close_count == 1


def test_provider_rejects_duplicate_tool_call_ids() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "duplicate",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "duplicate",
                            "type": "function",
                            "function": {"name": "list_files", "arguments": "{}"},
                        },
                    ],
                }
            }
        ]
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    ) as http_client:
        provider = OpenAICompatibleClient(
            "https://example.test/v1",
            "fake-model",
            "offline-secret",
            http_client=http_client,
        )

        with pytest.raises(ModelProtocolError, match="tool call ids must be unique"):
            provider.complete((), ())


def test_streaming_provider_rejects_duplicate_tool_call_ids() -> None:
    events = (
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "duplicate",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "duplicate",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        },
    )
    content = "".join(
        f"data: {json.dumps(event)}\n\n" for event in events
    ) + "data: [DONE]\n\n"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=content.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
    ) as http_client:
        provider = OpenAICompatibleClient(
            "https://example.test/v1",
            "fake-model",
            "offline-secret",
            http_client=http_client,
        )

        with pytest.raises(ModelProtocolError, match="tool call ids must be unique"):
            provider.complete_streaming((), (), lambda _chunk: None)


def _config(workspace) -> RuntimeConfig:
    return RuntimeConfig(
        workspace.resolve(),
        "https://example.test/v1",
        "fake-model",
        "offline-secret",
        "OFFLINE_KEY",
        "disabled",
        frozenset({"OFFLINE_KEY"}),
        8,
        20_000,
        4,
        2_000,
        5,
    )

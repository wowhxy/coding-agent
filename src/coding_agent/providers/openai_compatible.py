"""Synchronous OpenAI-compatible chat-completions adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from ..model import ModelProtocolError, ModelTransportError
from ..protocol import Message, ModelTurn, Role, ToolCall, ToolDefinition


MAX_ATTEMPTS = 3
RETRY_DELAYS = (0.25, 0.5)
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class OpenAICompatibleClient:
    """Translate the internal protocol to OpenAI-compatible HTTP requests."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        thinking_mode: str = "provider-default",
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        if thinking_mode not in {"provider-default", "disabled"}:
            raise ValueError(
                "thinking_mode must be 'provider-default' or 'disabled'"
            )
        self._thinking_mode = thinking_mode
        self._sleep = sleep
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(timeout=30.0)
        self._closed = False

    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        """Return one normalized model turn after bounded retries."""

        payload = {
            "model": self._model,
            "messages": [_message_payload(message) for message in messages],
            "tools": [
                _tool_definition_payload(definition)
                for definition in tool_definitions
            ],
            "stream": False,
        }
        if self._thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._http_client.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
            except httpx.TransportError as exc:
                if attempt < MAX_ATTEMPTS:
                    self._sleep(RETRY_DELAYS[attempt - 1])
                    continue
                raise ModelTransportError(
                    "model request failed after 3 transport attempts: "
                    f"{type(exc).__name__}"
                ) from None
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if (
                    status_code in RETRYABLE_STATUS_CODES
                    and attempt < MAX_ATTEMPTS
                ):
                    self._sleep(RETRY_DELAYS[attempt - 1])
                    continue
                raise ModelTransportError(
                    "model request failed with HTTP status "
                    f"{status_code} after {attempt} attempt(s)"
                ) from None

            return _parse_response(response)

        raise AssertionError("bounded retry loop ended unexpectedly")

    def complete_streaming(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
        text_sink: Callable[[str], None],
    ) -> ModelTurn:
        """Stream assistant text and assemble one normalized model turn."""

        payload = {
            "model": self._model,
            "messages": [_message_payload(message) for message in messages],
            "tools": [
                _tool_definition_payload(definition) for definition in tool_definitions
            ],
            "stream": True,
        }
        if self._thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        emitted = False

        def emit(chunk: str) -> None:
            nonlocal emitted
            emitted = True
            text_sink(chunk)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self._http_client.stream(
                    "POST", self._endpoint, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    return _parse_stream(response.iter_lines(), emit)
            except httpx.TransportError as exc:
                if not emitted and attempt < MAX_ATTEMPTS:
                    self._sleep(RETRY_DELAYS[attempt - 1])
                    continue
                raise ModelTransportError(
                    f"model stream failed after {attempt} transport attempt(s): "
                    f"{type(exc).__name__}"
                ) from None
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if (
                    not emitted
                    and status_code in RETRYABLE_STATUS_CODES
                    and attempt < MAX_ATTEMPTS
                ):
                    self._sleep(RETRY_DELAYS[attempt - 1])
                    continue
                raise ModelTransportError(
                    "model stream failed with HTTP status "
                    f"{status_code} after {attempt} attempt(s)"
                ) from None

        raise AssertionError("bounded retry loop ended unexpectedly")

    def close(self) -> None:
        """Close an internally created HTTP client at most once."""

        if not self._owns_http_client or self._closed:
            return
        self._closed = True
        self._http_client.close()


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.role is Role.ASSISTANT and message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_json,
                },
            }
            for call in message.tool_calls
        ]
    if message.role is Role.TOOL:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _tool_definition_payload(
    definition: ToolDefinition,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.input_schema,
        },
    }


def _parse_response(response: httpx.Response) -> ModelTurn:
    try:
        payload = response.json()
    except ValueError:
        raise ModelProtocolError(
            "model response was not valid JSON"
        ) from None

    if not isinstance(payload, dict):
        raise ModelProtocolError("model response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelProtocolError(
            "model response requires a non-empty choices array"
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelProtocolError("model response choice must be an object")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelProtocolError(
            "model response choice requires a message object"
        )
    if message.get("role") != "assistant":
        raise ModelProtocolError(
            "model response message role must be assistant"
        )

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ModelProtocolError(
            "model response message content must be a string or null"
        )

    raw_tool_calls = message.get("tool_calls", [])
    if raw_tool_calls is None:
        raw_tool_calls = []
    if not isinstance(raw_tool_calls, list):
        raise ModelProtocolError(
            "model response tool_calls must be an array"
        )
    tool_calls = tuple(
        _parse_tool_call(raw_call) for raw_call in raw_tool_calls
    )
    if not content and not tool_calls:
        raise ModelProtocolError(
            "model response contained neither content nor tool calls"
        )
    return ModelTurn(final_text=content, tool_calls=tool_calls)


def _parse_tool_call(raw_call: object) -> ToolCall:
    if not isinstance(raw_call, dict):
        raise ModelProtocolError("model tool call must be an object")
    call_id = raw_call.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise ModelProtocolError("model tool call requires a string id")
    if raw_call.get("type") != "function":
        raise ModelProtocolError("model tool call type must be function")
    function = raw_call.get("function")
    if not isinstance(function, dict):
        raise ModelProtocolError(
            "model tool call requires a function object"
        )
    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name:
        raise ModelProtocolError(
            "model tool call requires a string function name"
        )
    if not isinstance(arguments, str):
        raise ModelProtocolError(
            "model tool call requires string function arguments"
        )
    return ToolCall(call_id, name, arguments)


def _parse_stream(lines: Any, text_sink: Callable[[str], None]) -> ModelTurn:
    content_parts: list[str] = []
    fragments: dict[int, dict[str, str]] = {}
    done = False
    for line in lines:
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            raise ModelProtocolError("model stream contained an invalid SSE event")
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        try:
            payload = json.loads(data)
        except ValueError:
            raise ModelProtocolError("model stream event was not valid JSON") from None
        if not isinstance(payload, dict):
            raise ModelProtocolError("model stream event must be a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProtocolError("model stream event requires a choice")
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            raise ModelProtocolError("model stream choice requires a delta object")
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise ModelProtocolError("model stream content must be a string or null")
            if content:
                content_parts.append(content)
                text_sink(content)
        raw_calls = delta.get("tool_calls")
        if raw_calls is not None:
            if not isinstance(raw_calls, list):
                raise ModelProtocolError("model stream tool_calls must be an array")
            for raw_call in raw_calls:
                _merge_tool_call_fragment(fragments, raw_call)
    if not done:
        raise ModelProtocolError("model stream ended before the done event")
    calls = tuple(
        ToolCall(parts["id"], parts["name"], parts["arguments"])
        for _, parts in sorted(fragments.items())
        if _validate_streamed_tool_call(parts)
    )
    content = "".join(content_parts) or None
    if content is None and not calls:
        raise ModelProtocolError("model stream contained neither content nor tool calls")
    return ModelTurn(content, calls)


def _merge_tool_call_fragment(
    fragments: dict[int, dict[str, str]], raw_call: object
) -> None:
    if not isinstance(raw_call, dict):
        raise ModelProtocolError("model stream tool call must be an object")
    index = raw_call.get("index")
    if type(index) is not int or index < 0:
        raise ModelProtocolError("model stream tool call requires an integer index")
    parts = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
    call_id = raw_call.get("id")
    if call_id is not None:
        if not isinstance(call_id, str):
            raise ModelProtocolError("model stream tool call id must be a string")
        parts["id"] += call_id
    call_type = raw_call.get("type")
    if call_type is not None and call_type != "function":
        raise ModelProtocolError("model stream tool call type must be function")
    function = raw_call.get("function")
    if function is not None:
        if not isinstance(function, dict):
            raise ModelProtocolError("model stream function must be an object")
        for source, target in (("name", "name"), ("arguments", "arguments")):
            value = function.get(source)
            if value is not None:
                if not isinstance(value, str):
                    raise ModelProtocolError(
                        f"model stream function {source} must be a string"
                    )
                parts[target] += value


def _validate_streamed_tool_call(parts: dict[str, str]) -> bool:
    if not parts["id"] or not parts["name"]:
        raise ModelProtocolError("model stream tool call is incomplete")
    return True

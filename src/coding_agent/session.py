"""Strict, provider-neutral session record serialization and redaction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .protocol import Message, Role, ToolCall


SESSION_SCHEMA_VERSION = 1

_ROOT_FIELDS = {
    "schema_version",
    "session_id",
    "workspace",
    "provider",
    "model",
    "created_at",
    "updated_at",
    "messages",
}
_SESSION_ID = re.compile(r"[0-9a-f]{12}")


class SessionError(Exception):
    """A concise, recoverable error while reading or writing a session."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.error_code}: {self.message}"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """The complete provider-neutral history and metadata for one session."""

    session_id: str
    workspace: Path
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime
    messages: tuple[Message, ...]


def serialize_session(record: SessionRecord) -> str:
    """Validate and serialize a session record into the v1 JSON document."""

    _validate_record(record)
    payload = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": record.session_id,
        "workspace": str(record.workspace),
        "provider": record.provider,
        "model": record.model,
        "created_at": _format_timestamp(record.created_at),
        "updated_at": _format_timestamp(record.updated_at),
        "messages": [_message_to_payload(message) for message in record.messages],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def deserialize_session(text: str) -> SessionRecord:
    """Parse and strictly validate a v1 JSON session document."""

    if type(text) is not str:
        _corrupt("session document must be text")
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except ValueError:
        _corrupt("session document is not valid JSON")

    if type(payload) is not dict:
        _corrupt("session document root must be an object")
    if "schema_version" not in payload:
        _corrupt("session schema version is missing")

    version = payload["schema_version"]
    if type(version) in (int, float) and version != SESSION_SCHEMA_VERSION:
        raise SessionError("SESSION_VERSION_UNSUPPORTED", "session schema version is unsupported")
    if type(version) is not int or version != SESSION_SCHEMA_VERSION:
        _corrupt("session schema version is invalid")
    if set(payload) != _ROOT_FIELDS:
        _corrupt("session document fields are invalid")

    session_id = _parse_session_id(payload["session_id"])
    workspace = _parse_workspace(payload["workspace"])
    provider = _parse_name(payload["provider"], "provider")
    model = _parse_name(payload["model"], "model")
    created_at = _parse_timestamp(payload["created_at"])
    updated_at = _parse_timestamp(payload["updated_at"])
    messages = _parse_messages(payload["messages"])

    record = SessionRecord(
        session_id=session_id,
        workspace=workspace,
        provider=provider,
        model=model,
        created_at=created_at,
        updated_at=updated_at,
        messages=messages,
    )
    _validate_record(record)
    return record


def redact_messages(
    messages: tuple[Message, ...], sensitive_values: tuple[str, ...]
) -> tuple[Message, ...]:
    """Return an immutable copy with known non-empty sensitive values replaced."""

    values = tuple(value for value in sensitive_values if type(value) is str and value)
    if not values:
        return messages

    def redact(value: str | None) -> str | None:
        if value is None:
            return None
        for sensitive in values:
            value = value.replace(sensitive, "[REDACTED]")
        return value

    return tuple(
        Message(
            role=message.role,
            content=redact(message.content),
            tool_calls=tuple(
                ToolCall(
                    id=redact(call.id) or "",
                    name=redact(call.name) or "",
                    arguments_json=redact(call.arguments_json) or "",
                )
                for call in message.tool_calls
            ),
            tool_call_id=redact(message.tool_call_id),
        )
        for message in messages
    )


def _validate_record(record: SessionRecord) -> None:
    if not isinstance(record, SessionRecord):
        _corrupt("session record is invalid")
    _parse_session_id(record.session_id)
    if not isinstance(record.workspace, Path) or not record.workspace.is_absolute():
        _corrupt("workspace must be an absolute path")
    _parse_name(record.provider, "provider")
    _parse_name(record.model, "model")
    _format_timestamp(record.created_at)
    _format_timestamp(record.updated_at)
    _validate_messages(record.messages)


def _parse_session_id(value: Any) -> str:
    if type(value) is not str or _SESSION_ID.fullmatch(value) is None:
        _corrupt("session id is invalid")
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constants are not supported")


def _parse_workspace(value: Any) -> Path:
    if type(value) is not str:
        _corrupt("workspace is invalid")
    workspace = Path(value)
    if not workspace.is_absolute():
        _corrupt("workspace must be an absolute path")
    return workspace


def _parse_name(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        _corrupt(f"{field} is invalid")
    return value


def _format_timestamp(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _corrupt("timestamp must be an aware UTC datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        _corrupt("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        _corrupt("timestamp is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _corrupt("timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _message_to_payload(message: Message) -> dict[str, Any]:
    if message.role is Role.USER:
        return {"role": message.role.value, "content": message.content}
    if message.role is Role.ASSISTANT and message.tool_calls:
        return {
            "role": message.role.value,
            "content": message.content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments_json": call.arguments_json}
                for call in message.tool_calls
            ],
        }
    if message.role is Role.ASSISTANT:
        return {"role": message.role.value, "content": message.content}
    if message.role is Role.TOOL:
        return {
            "role": message.role.value,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    _corrupt("system messages cannot be persisted")


def _parse_messages(value: Any) -> tuple[Message, ...]:
    if type(value) is not list or not value:
        _corrupt("messages must be a non-empty array")
    messages = tuple(_parse_message(item) for item in value)
    _validate_messages(messages)
    return messages


def _parse_message(value: Any) -> Message:
    if type(value) is not dict or type(value.get("role")) is not str:
        _corrupt("message is invalid")
    role = value["role"]
    if role == Role.USER.value:
        _require_fields(value, {"role", "content"})
        if type(value["content"]) is not str:
            _corrupt("user message content is invalid")
        return Message(Role.USER, value["content"])
    if role == Role.ASSISTANT.value:
        if set(value) == {"role", "content"}:
            if type(value["content"]) is not str or not value["content"]:
                _corrupt("final assistant message content is invalid")
            return Message(Role.ASSISTANT, value["content"])
        _require_fields(value, {"role", "content", "tool_calls"})
        if value["content"] is not None and type(value["content"]) is not str:
            _corrupt("tool-calling assistant content is invalid")
        if type(value["tool_calls"]) is not list or not value["tool_calls"]:
            _corrupt("tool calls are invalid")
        calls = tuple(_parse_tool_call(call) for call in value["tool_calls"])
        return Message(Role.ASSISTANT, value["content"], calls)
    if role == Role.TOOL.value:
        _require_fields(value, {"role", "content", "tool_call_id"})
        if type(value["content"]) is not str or type(value["tool_call_id"]) is not str:
            _corrupt("tool message fields are invalid")
        return Message(Role.TOOL, value["content"], (), value["tool_call_id"])
    _corrupt("message role is invalid")


def _parse_tool_call(value: Any) -> ToolCall:
    _require_fields(value, {"id", "name", "arguments_json"})
    if any(type(value[field]) is not str for field in ("id", "name", "arguments_json")):
        _corrupt("tool call fields are invalid")
    if not value["id"]:
        _corrupt("tool call id is invalid")
    return ToolCall(value["id"], value["name"], value["arguments_json"])


def _validate_messages(messages: Any) -> None:
    if type(messages) is not tuple or not messages:
        _corrupt("messages must be a non-empty tuple")
    pending: set[str] | None = None
    completed: set[str] = set()
    requires_new_user = False
    for index, message in enumerate(messages):
        if not isinstance(message, Message) or not isinstance(message.role, Role):
            _corrupt("message is invalid")
        if index == 0 and message.role is not Role.USER:
            _corrupt("first message must be a user message")
        if requires_new_user and message.role is not Role.USER:
            _corrupt("final assistant message must be followed by a user message")
        _validate_message_shape(message)
        if pending is not None:
            if message.role is not Role.TOOL or message.tool_call_id not in pending or message.tool_call_id in completed:
                _corrupt("tool result batch is invalid")
            completed.add(message.tool_call_id)
            if completed == pending:
                pending = None
                completed = set()
        elif message.role is Role.TOOL:
            _corrupt("tool result has no pending tool call")
        elif message.role is Role.ASSISTANT and message.tool_calls:
            identifiers = [call.id for call in message.tool_calls]
            if len(set(identifiers)) != len(identifiers):
                _corrupt("tool call ids must be distinct")
            pending = set(identifiers)
        if message.role is Role.USER:
            requires_new_user = False
        elif message.role is Role.ASSISTANT and not message.tool_calls:
            requires_new_user = True
    if pending is not None:
        _corrupt("terminal tool result batch is incomplete")


def _validate_message_shape(message: Message) -> None:
    if message.role is Role.SYSTEM:
        _corrupt("system messages cannot be persisted")
    if message.role is Role.USER:
        if type(message.content) is not str or message.tool_calls or message.tool_call_id is not None:
            _corrupt("user message is invalid")
        return
    if message.role is Role.ASSISTANT:
        if message.tool_call_id is not None:
            _corrupt("assistant message is invalid")
        if message.tool_calls:
            if message.content is not None and type(message.content) is not str:
                _corrupt("tool-calling assistant message is invalid")
            for call in message.tool_calls:
                if not isinstance(call, ToolCall) or any(
                    type(field) is not str for field in (call.id, call.name, call.arguments_json)
                ) or not call.id:
                    _corrupt("tool call is invalid")
            return
        if type(message.content) is not str or not message.content:
            _corrupt("final assistant message is invalid")
        return
    if message.role is Role.TOOL:
        if type(message.content) is not str or type(message.tool_call_id) is not str or message.tool_calls:
            _corrupt("tool message is invalid")
        return
    _corrupt("message role is invalid")


def _require_fields(value: Any, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        _corrupt("object fields are invalid")


def _corrupt(message: str) -> None:
    raise SessionError("SESSION_CORRUPT", message)

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding_agent.protocol import Message, Role, ToolCall
from coding_agent.session import (
    SESSION_SCHEMA_VERSION,
    SessionError,
    SessionRecord,
    deserialize_session,
    redact_messages,
    serialize_session,
)


WORKSPACE = Path("C:/coding-agent-workspace")
CREATED_AT = datetime(2026, 8, 27, 9, 30, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 8, 27, 9, 35, tzinfo=timezone.utc)


def valid_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_id": "a1b2c3d4e5f6",
        "workspace": str(WORKSPACE),
        "provider": "openai-compatible",
        "model": "demo-model",
        "created_at": "2026-08-27T09:30:00Z",
        "updated_at": "2026-08-27T09:35:00Z",
        "messages": [
            {"role": "user", "content": "Inspect 你好"},
            {
                "role": "assistant",
                "content": "I will inspect it.",
                "tool_calls": [
                    {"id": "call-read", "name": "read_file", "arguments_json": '{"path":"src/main.py"}'},
                    {"id": "call-list", "name": "list_files", "arguments_json": "{}"},
                ],
            },
            {"role": "tool", "content": "print('ok')", "tool_call_id": "call-read"},
            {"role": "tool", "content": "src/main.py", "tool_call_id": "call-list"},
            {"role": "user", "content": "Now summarize."},
            {"role": "assistant", "content": "完成了。"},
        ],
    }


def valid_record() -> SessionRecord:
    return SessionRecord(
        session_id="a1b2c3d4e5f6",
        workspace=WORKSPACE,
        provider="openai-compatible",
        model="demo-model",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
        messages=(
            Message(Role.USER, "Inspect 你好"),
            Message(
                Role.ASSISTANT,
                "I will inspect it.",
                (
                    ToolCall("call-read", "read_file", '{"path":"src/main.py"}'),
                    ToolCall("call-list", "list_files", "{}"),
                ),
            ),
            Message(Role.TOOL, "print('ok')", (), "call-read"),
            Message(Role.TOOL, "src/main.py", (), "call-list"),
            Message(Role.USER, "Now summarize."),
            Message(Role.ASSISTANT, "完成了。"),
        ),
    )


def encode(document: object) -> str:
    return json.dumps(document, ensure_ascii=False)


def assert_corrupt(document: object) -> None:
    with pytest.raises(SessionError) as raised:
        deserialize_session(encode(document))
    assert raised.value.error_code == "SESSION_CORRUPT"


def test_session_round_trip_preserves_protocol_fields_and_canonical_json() -> None:
    record = valid_record()

    encoded = serialize_session(record)
    payload = json.loads(encoded)

    assert set(payload) == {
        "schema_version",
        "session_id",
        "workspace",
        "provider",
        "model",
        "created_at",
        "updated_at",
        "messages",
    }
    assert payload["schema_version"] == SESSION_SCHEMA_VERSION == 1
    assert payload["created_at"] == "2026-08-27T09:30:00Z"
    assert payload["updated_at"] == "2026-08-27T09:35:00Z"
    assert "你好" in encoded and "\\u4f60" not in encoded
    assert encoded.startswith("{\n  ")
    assert deserialize_session(encoded) == record


def test_serialize_rejects_system_messages_and_non_utc_datetimes() -> None:
    system_record = SessionRecord(
        "a1b2c3d4e5f6", WORKSPACE, "provider", "model", CREATED_AT, UPDATED_AT,
        (Message(Role.SYSTEM, "never persist"),),
    )
    naive_record = SessionRecord(
        "a1b2c3d4e5f6", WORKSPACE, "provider", "model",
        datetime(2026, 8, 27, 9, 30), UPDATED_AT, (Message(Role.USER, "hi"),),
    )
    offset_record = SessionRecord(
        "a1b2c3d4e5f6", WORKSPACE, "provider", "model",
        datetime(2026, 8, 27, 10, 30, tzinfo=timezone(timedelta(hours=1))), UPDATED_AT,
        (Message(Role.USER, "hi"),),
    )

    for record in (system_record, naive_record, offset_record):
        with pytest.raises(SessionError) as raised:
            serialize_session(record)
        assert raised.value.error_code == "SESSION_CORRUPT"


def test_redaction_replaces_all_message_string_fields_without_mutating_input() -> None:
    secret = "fake-sensitive-value"
    messages = (
        Message(Role.USER, f"input {secret}"),
        Message(
            Role.ASSISTANT,
            f"thinking {secret}",
            (ToolCall(f"id-{secret}", f"name-{secret}", f'{{"token":"{secret}"}}'),),
        ),
        Message(Role.TOOL, f"result {secret}", (), f"id-{secret}"),
    )

    redacted = redact_messages(messages, ("", secret))

    assert redacted != messages
    assert messages[0].content == f"input {secret}"
    assert messages[1].tool_calls[0].arguments_json == f'{{"token":"{secret}"}}'
    assert redacted[0].content == "input [REDACTED]"
    assert redacted[1].content == "thinking [REDACTED]"
    assert redacted[1].tool_calls[0] == ToolCall(
        "id-[REDACTED]", "name-[REDACTED]", '{"token":"[REDACTED]"}'
    )
    assert redacted[2] == Message(Role.TOOL, "result [REDACTED]", (), "id-[REDACTED]")
    redacted_record = SessionRecord(
        "a1b2c3d4e5f6", WORKSPACE, "provider", "model", CREATED_AT, UPDATED_AT, redacted
    )
    assert secret not in serialize_session(redacted_record)


@pytest.mark.parametrize(
    ("text", "error_code"),
    [
        ("{", "SESSION_CORRUPT"),
        ("[]", "SESSION_CORRUPT"),
        (encode({**valid_document(), "schema_version": 2}), "SESSION_VERSION_UNSUPPORTED"),
        (encode({**valid_document(), "schema_version": 2.0}), "SESSION_VERSION_UNSUPPORTED"),
        (encode({**valid_document(), "schema_version": "1"}), "SESSION_CORRUPT"),
    ],
)
def test_deserialize_rejects_invalid_document_envelope(text: str, error_code: str) -> None:
    with pytest.raises(SessionError) as raised:
        deserialize_session(text)
    assert raised.value.error_code == error_code


def test_deserialize_classifies_future_numeric_versions_before_v1_field_validation() -> None:
    future_document = {
        "schema_version": 2,
        "v2_messages": [],
    }

    with pytest.raises(SessionError) as raised:
        deserialize_session(encode(future_document))

    assert raised.value.error_code == "SESSION_VERSION_UNSUPPORTED"


def test_deserialize_classifies_missing_schema_version_as_corrupt() -> None:
    document = valid_document()
    del document["schema_version"]

    with pytest.raises(SessionError) as raised:
        deserialize_session(encode(document))

    assert raised.value.error_code == "SESSION_CORRUPT"
    assert raised.value.message == "session schema version is missing"


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_deserialize_rejects_nonstandard_json_constants_before_duplicate_key_overwrite(
    constant: str,
) -> None:
    encoded = encode(valid_document())
    nonstandard = encoded.replace(
        '"schema_version": 1,',
        f'"schema_version": {constant}, "schema_version": 1,',
        1,
    )
    assert nonstandard != encoded

    with pytest.raises(SessionError) as raised:
        deserialize_session(nonstandard)

    assert raised.value.error_code == "SESSION_CORRUPT"


@pytest.mark.parametrize("version", [True, "2", None])
def test_deserialize_treats_non_numeric_schema_versions_as_corrupt(version: object) -> None:
    document = valid_document()
    document["schema_version"] = version

    assert_corrupt(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update({"unexpected": True}),
        lambda doc: doc.pop("model"),
        lambda doc: doc.update({"session_id": "A1B2C3D4E5F6"}),
        lambda doc: doc.update({"session_id": "short"}),
        lambda doc: doc.update({"workspace": "relative/path"}),
        lambda doc: doc.update({"provider": "   "}),
        lambda doc: doc.update({"model": ""}),
        lambda doc: doc.update({"created_at": "2026-08-27T09:30:00+00:00"}),
        lambda doc: doc.update({"updated_at": "not-a-timestamp"}),
        lambda doc: doc.update({"messages": {}}),
        lambda doc: doc.update({"messages": []}),
    ],
)
def test_deserialize_rejects_invalid_metadata_and_container_shapes(mutate) -> None:
    document = valid_document()
    mutate(document)
    assert_corrupt(document)


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "assistant", "content": "first"}],
        [{"role": "system", "content": "forbidden"}],
        [{"role": "user", "content": "ok", "extra": True}],
        [{"role": "user", "content": None}],
        [{"role": "assistant", "content": ""}],
        [{"role": "assistant", "content": None, "tool_calls": []}],
        [{"role": "assistant", "content": 7, "tool_calls": [{"id": "x", "name": "n", "arguments_json": "{}"}]}],
        [{"role": "tool", "content": "result", "tool_call_id": "x"}],
    ],
)
def test_deserialize_rejects_wrong_role_specific_message_shapes(messages: list[dict[str, object]]) -> None:
    document = valid_document()
    document["messages"] = messages
    assert_corrupt(document)


@pytest.mark.parametrize(
    "tool_calls",
    [
        [{"id": "x", "name": "n", "arguments_json": "{}", "extra": 1}],
        [{"id": "", "name": "n", "arguments_json": "{}"}],
        [{"id": "x", "name": 1, "arguments_json": "{}"}],
        [
            {"id": "same", "name": "a", "arguments_json": "not-json"},
            {"id": "same", "name": "b", "arguments_json": "{}"},
        ],
    ],
)
def test_deserialize_rejects_malformed_or_duplicate_tool_calls(tool_calls: list[dict[str, object]]) -> None:
    document = valid_document()
    document["messages"] = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
    ]
    assert_corrupt(document)


@pytest.mark.parametrize(
    "messages",
    [
        [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "name": "x", "arguments_json": "{}"}]},
            {"role": "tool", "content": "out", "tool_call_id": "unknown"},
        ],
        [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "name": "x", "arguments_json": "{}"}]},
            {"role": "tool", "content": "one", "tool_call_id": "a"},
            {"role": "tool", "content": "two", "tool_call_id": "a"},
        ],
        [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "name": "x", "arguments_json": "{}"}, {"id": "b", "name": "y", "arguments_json": "{}"}]},
            {"role": "tool", "content": "one", "tool_call_id": "a"},
            {"role": "user", "content": "interrupt"},
        ],
        [
            {"role": "user", "content": "do it"},
            {"role": "tool", "content": "orphan", "tool_call_id": "a"},
        ],
        [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "name": "x", "arguments_json": "{}"}]},
        ],
    ],
)
def test_deserialize_enforces_complete_tool_result_batches(messages: list[dict[str, object]]) -> None:
    document = valid_document()
    document["messages"] = messages
    assert_corrupt(document)


def test_deserialize_rejects_assistant_message_after_final_assistant_until_next_user() -> None:
    document = valid_document()
    document["messages"] = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "final answer"},
        {"role": "assistant", "content": "another final answer"},
    ]

    assert_corrupt(document)


def test_deserialize_allows_consecutive_users_and_complete_terminal_tool_batch() -> None:
    document = valid_document()
    document["messages"] = [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "retry after model error"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "name": "x", "arguments_json": "not-json"}]},
        {"role": "tool", "content": "done", "tool_call_id": "a"},
    ]

    decoded = deserialize_session(encode(document))

    assert decoded.messages[-1] == Message(Role.TOOL, "done", (), "a")


def test_errors_are_concise_and_do_not_expose_sensitive_document_content() -> None:
    secret = "fake-sensitive-value"
    document = valid_document()
    document[secret] = True

    with pytest.raises(SessionError) as raised:
        deserialize_session(encode(document))

    assert secret not in str(raised.value)
    assert "Traceback" not in str(raised.value)

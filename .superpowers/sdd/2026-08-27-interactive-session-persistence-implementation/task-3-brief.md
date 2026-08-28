### Task 3: Strict Session Record Codec and Redaction

**Files:**
- Create `src/coding_agent/session.py`
- Create `tests/test_session.py`

**Public interfaces:**
- `SESSION_SCHEMA_VERSION = 1`
- `SessionError(error_code: str, message: str)`, with public `error_code`, concise `str()`, and no traceback formatting
- frozen/slots `SessionRecord(session_id: str, workspace: Path, provider: str, model: str, created_at: datetime, updated_at: datetime, messages: tuple[Message, ...])`
- `serialize_session(record: SessionRecord) -> str`
- `deserialize_session(text: str) -> SessionRecord`
- `redact_messages(messages: tuple[Message, ...], sensitive_values: tuple[str, ...]) -> tuple[Message, ...]`

#### Step 1: Add failing round-trip and redaction tests

Build one absolute-workspace record containing Unicode user/final content, assistant content plus multiple tool calls, paired tool messages, and later turns. Assert:

- JSON exact root fields are `schema_version`, `session_id`, `workspace`, `provider`, `model`, `created_at`, `updated_at`, `messages`;
- all Message and ToolCall fields round-trip;
- output uses schema version 1, UTF-8-compatible `ensure_ascii=False`, 2-space indentation, and UTC timestamps ending `Z`;
- no system message can serialize;
- replacing a non-empty fake sensitive value covers every Message string field (`content`, `tool_call_id`, ToolCall `id`, `name`, `arguments_json`) without mutating input;
- empty sensitive strings are ignored;
- neither serialized output nor raised errors expose the fake sensitive value.

#### Step 2: Add failing corruption tests

Parameterize malformed JSON, non-object root, unknown/missing root fields, unsupported schema version, invalid non-12-lowercase-hex session ID, non-absolute workspace, empty/invalid provider or model, invalid/non-UTC timestamps, non-array or empty messages, first role not user, system role, unknown message fields, wrong role-specific fields/types, malformed/duplicate tool-call IDs, unknown/duplicate/missing tool results, tool result outside a pending batch, and an incomplete terminal tool-call batch.

Stable errors:

- unsupported numeric schema version → `SESSION_VERSION_UNSUPPORTED`;
- every other session document/schema/protocol defect → `SESSION_CORRUPT`.

Messages must use exact role-specific object shapes:

- user: `role`, `content` where content is a string;
- final assistant: `role`, `content` where content is a non-empty string;
- tool-calling assistant: `role`, `content`, `tool_calls`; content is string or null, tool_calls is non-empty and each call has exact string `id`, `name`, `arguments_json` fields;
- tool: `role`, `content`, `tool_call_id`, all string fields.

Persisted sequences start with user and never contain system. A pending assistant tool-call batch must be followed by exactly one tool message for every distinct ID before another user/assistant message. After a complete batch the next assistant model step or next user turn is allowed. Consecutive user messages are allowed because MODEL_ERROR may commit a user-only turn. Valid terminal states are user, final assistant, or a complete tool-result batch.

Do not require `arguments_json` to decode as an object; it is provider-neutral unparsed JSON text in the existing protocol.

#### Step 3: Verify RED

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-red" tests/test_session.py
```

Expected initial failure: `coding_agent.session` does not exist. Record it.

#### Step 4: Minimal implementation

Use standard-library dataclass/json/datetime/path only. Validate through small focused helpers and strict exact-key comparisons. Store the absolute workspace `Path` as a string. Format aware UTC datetimes with a final `Z`; reject naive or non-UTC values on serialization and parse only strings ending `Z` into aware UTC datetimes. Validate before serialization as well as after parsing. Use `json.dumps(payload, ensure_ascii=False, indent=2)`.

Redaction uses deterministic exact substring replacement with `[REDACTED]`; filter empty sensitive values first, do not mutate the tuple or its frozen messages/tool calls, and do not add a generic secret scanner.

#### Step 5: Verify GREEN and regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-target" tests/test_session.py tests/test_protocol.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-full"
```

All targeted and full tests must pass offline.

#### Step 6: Self-review/report

Review schema exactness, role grammar, valid terminal states, pairing, non-mutation, system exclusion, timestamp handling, and error secrecy. Do not implement disk paths/index/atomic writes (Task 4).

No Git operations. Write the complete report and exact RED/GREEN evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-3-report.md`.

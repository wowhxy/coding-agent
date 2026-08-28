# Interactive Session Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, locally persisted, multi-turn interactive session mode while preserving the existing one-shot Coding Agent behavior.

**Architecture:** Extend `AgentRunner` with a one-user-turn API over a copyable `ConversationHistory`; keep `run()` as the one-shot compatibility wrapper. Persist system-free protocol messages in strict schema-v1 JSON through `JsonSessionStore`, and let a focused `InteractiveSession` own input, per-turn copy/commit, persistence, and exit behavior. CLI composition selects or creates sessions and reuses one provider client for the process.

**Tech Stack:** Python 3.11+, standard library JSON/path/hash/time/secret facilities, existing `httpx`, `pytest`, FakeModelClient. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-27-interactive-session-persistence-design.md`

## Global Constraints

- The PDF `推免考核题目学生版.pdf` remains the highest-priority requirement source.
- The existing one-shot CLI, Agent Loop, six tools, ToolRegistry, provider abstraction, FakeModelClient, protocol statuses, context anchors, and Demo remain stable.
- Core conversation, context, session orchestration, serialization, persistence, termination, and error handling are implemented locally without an Agent Framework or Agent SDK.
- Session memory is scoped to one selected session and one workspace; do not add cross-workspace memory, automatic summary, action journal, preference extraction, database, Web UI, multi-Agent, plugin system, streaming, multiline editing, or session management UI.
- Session files live outside the workspace and never contain the system prompt, Provider API Key, Authorization header, raw environment, or HTTP objects.
- The current Provider API Key is redacted before persistence; `RuntimeConfig.api_key` remains `repr=False`.
- Context retains the current system prompt, first session user message, and latest user-led turn; it deterministically evicts only older turns and continues truncating tool output.
- `FINAL_RESPONSE` ends one user turn in interactive mode, not the interactive process and not semantic correctness.
- Tests are offline, deterministic, and use FakeModelClient, fake input/output, fake clock/ID sources, and pytest temporary paths.
- Every task starts with a failing test, uses the smallest implementation that passes it, runs targeted tests and the complete regression suite, and receives task-scoped spec/quality review.
- All pytest commands use a task-specific `--basetemp` below the Codex-owned temporary root to avoid the known Windows global pytest ACL collision.
- Git writes, branch changes, commits, pushes, configuration changes, and history operations are forbidden. Record owner-managed checkpoint suggestions without pausing execution.

## File Structure

- Modify `src/coding_agent/context.py`: copyable/restorable canonical history and user-led context grouping.
- Modify `src/coding_agent/agent.py`: `run_turn()` and one-shot compatibility wrapper.
- Modify `src/coding_agent/system_prompt.py`: latest-user-language response policy.
- Create `src/coding_agent/session.py`: session record, strict schema-v1 codec, protocol validation, redaction.
- Create `src/coding_agent/session_store.py`: storage-home resolution, IDs, workspace index, atomic JSON persistence.
- Create `src/coding_agent/interactive.py`: synchronous multi-turn input/turn/commit/exit controller.
- Modify `src/coding_agent/cli.py`: optional task, interactive flags, session selection, one-client composition, exit code 7.
- Modify `README.txt` and `demo/README.txt`: compact interactive usage, resume semantics, plaintext-session warning.
- Modify existing tests and create focused session/store/interactive test modules.

---

### Task 1: Copyable Conversation History and One-Turn AgentRunner

**Files:**
- Modify: `src/coding_agent/context.py`
- Modify: `src/coding_agent/agent.py`
- Modify: `src/coding_agent/system_prompt.py`
- Modify: `tests/test_context.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `ConversationHistory(system_prompt: str, original_user_task: str | None = None)`.
- Produces: `ConversationHistory.from_persisted(system_prompt: str, messages: tuple[Message, ...]) -> ConversationHistory`.
- Produces: `ConversationHistory.copy() -> ConversationHistory`.
- Produces: `ConversationHistory.persisted_messages -> tuple[Message, ...]`, excluding the system message.
- Produces: `AgentRunner.run_turn(history: ConversationHistory, user_message: str) -> RunResult`.
- Preserves: `AgentRunner.run(system_prompt: str, original_user_task: str) -> RunResult`.

- [ ] **Step 1: Add failing history lifecycle tests**

Add tests proving an empty history contains only the system message, persisted recovery injects the supplied current system prompt, system roles in persisted messages are rejected, and copies do not share mutation:

```python
def test_history_copy_and_persisted_recovery_are_independent() -> None:
    original = ConversationHistory("current system", "first task")
    copy = original.copy()
    copy.append(Message(Role.ASSISTANT, "first answer"))

    restored = ConversationHistory.from_persisted(
        "new system",
        copy.persisted_messages,
    )

    assert [message.content for message in original.messages] == [
        "current system",
        "first task",
    ]
    assert restored.messages[0] == Message(Role.SYSTEM, "new system")
    assert restored.persisted_messages == copy.persisted_messages
```

- [ ] **Step 2: Add failing AgentRunner multi-turn tests**

Use one FakeModelClient script for two calls and assert both user messages and both final assistant messages remain canonical. Add a separate `max_steps=1` test proving each `run_turn` receives a fresh one-step budget:

```python
result_one = runner.run_turn(history, "first task")
result_two = runner.run_turn(history, "follow-up")

assert result_one.status is RunStatus.FINAL_RESPONSE
assert result_two.status is RunStatus.FINAL_RESPONSE
assert [message.content for message in history.messages] == [
    "system",
    "first task",
    "first answer",
    "follow-up",
    "second answer",
]
```

- [ ] **Step 3: Run Task 1 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-red" tests/test_context.py tests/test_agent.py tests/test_config.py
```

Expected: failures identify missing `copy`, `from_persisted`, `persisted_messages`, `run_turn`, final-message append, and latest-user-language policy.

- [ ] **Step 4: Implement the minimal history and run-turn API**

Implement the constructor compatibility and these methods without adding persistence concerns to `ConversationHistory`:

Use the exact signatures `ConversationHistory(system_prompt: str, original_user_task: str | None = None)`, `ConversationHistory.from_persisted(system_prompt: str, messages: tuple[Message, ...]) -> ConversationHistory`, and `ConversationHistory.copy() -> ConversationHistory`. The optional original task is appended only when non-`None`; recovery rejects an empty persisted tuple or any tuple whose first message is not a user message.

Move the existing loop body to `run_turn`, append the supplied user message before step 1, and append a final assistant Message before returning FINAL_RESPONSE. Keep failure fingerprints and step counters local to one call. Make `run()` create a system-only history and delegate once.

Add this system-prompt rule with no CLI-side translation:

```text
Unless the user explicitly requests another language, respond in the language of the latest user message.
```

- [ ] **Step 5: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-target" tests/test_context.py tests/test_agent.py tests/test_config.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-full"
```

Expected: all targeted and existing tests pass; the full suite has zero failures.

- [ ] **Step 6: Task review and checkpoint record**

Review exact Task 1 requirements, mutation isolation, one-shot compatibility, final-message persistence, per-turn counters, and scope. Apply reviewed fixes and re-run affected tests plus the full suite. Record checkpoint suggestion: files above; message `refactor: support reusable agent conversation turns`.

---

### Task 2: User-Led Deterministic Context Selection

**Files:**
- Modify: `src/coding_agent/context.py`
- Modify: `tests/test_context.py`

**Interfaces:**
- Consumes: Task 1 `ConversationHistory.messages` with one system message followed by one or more user-led turns.
- Preserves: `ContextManager.build(history) -> tuple[Message, ...]`.
- Produces: context that always retains system, first user, and the latest user-led turn or raises `ContextBudgetError`.

- [ ] **Step 1: Add failing multi-turn grouping tests**

Construct histories containing first-task assistant/tool tails and two later user turns. Assert a later user begins a new group, tool-call/result batches remain together, old groups evict first, and the latest user request is never silently removed:

```python
messages = manager.build(history)
contents = [message.content for message in messages]

assert contents[0:2] == ["system", "original task"]
assert "latest follow-up" in contents
assert "latest answer" in contents
assert "old follow-up" not in contents
```

Add a budget test where anchors plus the latest turn exceed the limit and assert `ContextBudgetError` rather than anchors-only output.

- [ ] **Step 2: Run Task 2 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-red" tests/test_context.py
```

Expected: current assistant-led grouping joins later user messages to the wrong group or drops the required latest turn.

- [ ] **Step 3: Implement user-led grouping and required-latest budgeting**

Replace the grouping helper with deterministic rules:

```python
def _group_user_led_turns(messages: tuple[Message, ...]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    for message in messages:
        if message.role is Role.USER or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns
```

Treat the last selected group as required. Validate anchors plus required latest group against the character budget before evicting older selected groups. With `recent_turns=1`, retain only the required latest group in addition to anchors.

- [ ] **Step 4: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-target" tests/test_context.py tests/test_agent.py tests/test_end_to_end.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-full"
```

Expected: all context, agent, end-to-end, and full-suite tests pass.

- [ ] **Step 5: Task review and checkpoint record**

Review original anchors, latest-turn guarantee, recent-turn count, tool-batch integrity, oversize error, and absence of summary/action-journal behavior. Apply reviewed fixes and re-run targeted plus full tests. Record this task with Task 1 in checkpoint `refactor: support reusable agent conversation turns`.

---

### Task 3: Strict Session Record Codec and Redaction

**Files:**
- Create: `src/coding_agent/session.py`
- Create: `tests/test_session.py`

**Interfaces:**
- Produces: `SESSION_SCHEMA_VERSION = 1`.
- Produces: `SessionError(error_code: str, message: str)` with concise `str()` and no traceback formatting.
- Produces: frozen `SessionRecord(session_id: str, workspace: Path, provider: str, model: str, created_at: datetime, updated_at: datetime, messages: tuple[Message, ...])`.
- Produces: `serialize_session(record: SessionRecord) -> str`.
- Produces: `deserialize_session(text: str) -> SessionRecord`.
- Produces: `redact_messages(messages: tuple[Message, ...], sensitive_values: tuple[str, ...]) -> tuple[Message, ...]`.

- [ ] **Step 1: Write failing schema round-trip and redaction tests**

Create a record with user messages, final assistant content, assistant content plus multiple tool calls, and paired tool messages. Assert UTF-8 JSON round-trip equality, no system role, and redaction across content, tool arguments, IDs/names only where the exact sensitive value appears:

```python
redacted = redact_messages(record.messages, ("provider-secret-value",))
serialized = serialize_session(replace(record, messages=redacted))

assert "provider-secret-value" not in serialized
assert "[REDACTED]" in serialized
assert deserialize_session(serialized).messages == redacted
```

- [ ] **Step 2: Write failing corruption validation tests**

Parameterize malformed JSON, non-object root, unknown/missing fields, unsupported version, invalid 12-hex ID, invalid timestamps, first role not user, system role, bad role fields, malformed tool call, unknown tool-call ID, duplicate/missing tool results, and incomplete terminal tool-call batches. Assert the exact stable code `SESSION_CORRUPT` or `SESSION_VERSION_UNSUPPORTED` and concise messages that contain no supplied fake key.

- [ ] **Step 3: Run Task 3 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-red" tests/test_session.py
```

Expected: import failure because `coding_agent.session` does not exist.

- [ ] **Step 4: Implement strict schema-v1 codec**

Use a frozen dataclass and strict exact-key checks. Store workspace as an absolute `Path` in memory and as a string in JSON. Serialize the complete schema payload with `json.dumps(payload, ensure_ascii=False, indent=2)` and parse ISO timestamps that end in `Z`. Do not serialize system messages.

Validate message grammar with a set of pending IDs for each assistant tool-call batch; allow histories ending in user, final assistant, or a complete tool-result batch. Replace every non-empty sensitive value in `Message.content`, `tool_call_id`, and each ToolCall string field without mutating the input tuple.

- [ ] **Step 5: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-target" tests/test_session.py tests/test_protocol.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task3-full"
```

Expected: codec, protocol, and full regression tests pass with zero credential-like matches.

- [ ] **Step 6: Task review and checkpoint record**

Review schema exactness, allowed terminal states, tool pairing, non-mutation, system exclusion, exact-value redaction, timestamps, and error secrecy. Apply reviewed fixes and repeat targeted plus full tests. Record checkpoint suggestion: `src/coding_agent/session.py`, `tests/test_session.py`; message `feat: add strict persisted session format`.

---

### Task 4: Atomic JsonSessionStore and Workspace Index

**Files:**
- Create: `src/coding_agent/session_store.py`
- Create: `tests/test_session_store.py`
- Modify: `src/coding_agent/session.py` only if Task 4 review requires a codec correction.

**Interfaces:**
- Consumes: Task 3 `SessionRecord`, `SessionError`, `serialize_session`, `deserialize_session`.
- Produces: `resolve_session_home(environ: Mapping[str, str] | None = None) -> Path`.
- Produces: `JsonSessionStore(root: Path, clock: Callable[[], datetime] = utc_now, id_generator: Callable[[], str] = generate_session_id)`.
- Produces: `create_session(workspace: Path, provider: str, model: str) -> SessionRecord` without immediate disk write.
- Produces: `load_latest(workspace: Path) -> SessionRecord | None`.
- Produces: `load_session(session_id: str, workspace: Path) -> SessionRecord`.
- Produces: `save(record: SessionRecord) -> SessionRecord`, returning updated UTC metadata after session-then-index writes.
- Produces the stable error codes `SESSION_NOT_FOUND`, `SESSION_CORRUPT`, `SESSION_VERSION_UNSUPPORTED`, `SESSION_WORKSPACE_MISMATCH`, `SESSION_INDEX_CORRUPT`, `SESSION_IO_ERROR`, and `SESSION_SAVE_FAILED` at the boundaries defined by the Spec.

- [ ] **Step 1: Write failing home, ID, and workspace-isolation tests**

Cover `CODING_AGENT_HOME`, Windows/macOS/Linux fallback routing through patched platform/environment values, canonical resolved/normcase workspace hashing, 12-lowercase-hex IDs, ID collision retry, no empty file on create, and workspace mismatch rejection.

- [ ] **Step 2: Write failing multiple-session and recovery tests**

Create two sessions for one workspace and one for another. Save all records and assert latest selection, retained `session_ids`, explicit old-session loading, cross-workspace rejection, unknown ID, malformed index, malformed session, and unsupported version stable errors.

- [ ] **Step 3: Write failing atomic-failure tests**

Inject a replace function or patch the narrow `_atomic_write_text` boundary. Assert:

```python
with pytest.raises(SessionError) as caught:
    store.save(updated_record)

assert caught.value.error_code == "SESSION_SAVE_FAILED"
assert old_session_path.read_text(encoding="utf-8") == old_session_text
```

Add a separate index-failure test proving the new session JSON is readable while the prior index remains byte-for-byte unchanged.

- [ ] **Step 4: Run Task 4 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-red" tests/test_session_store.py
```

Expected: import failure because `coding_agent.session_store` does not exist.

- [ ] **Step 5: Implement JsonSessionStore minimally**

Use `secrets.token_hex(6)` by default and reject nonconforming injected IDs. Resolve and validate workspace directories before hashing. Write each JSON document through a same-directory unique temporary file, flush, `os.fsync`, best-effort POSIX `chmod(0o600)`, then `os.replace`. Clean only the exact temporary file on failure.

Write session first and index second. Map codec/read/path errors to the exact Spec codes without exposing unrelated host paths or secrets. Preserve `created_at`; update `updated_at`, provider/model/messages supplied by the caller, and index ordering on save.

- [ ] **Step 6: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-target" tests/test_session.py tests/test_session_store.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-full"
```

Expected: all store, codec, and full-suite tests pass.

- [ ] **Step 7: Task review and checkpoint record**

Review cross-file non-transaction semantics, write order, exact cleanup target, index preservation, strict workspace matching, no empty-session write, path portability, and error codes. Apply reviewed fixes and repeat tests. Record Task 3–4 checkpoint message `feat: persist workspace sessions as atomic JSON` with both new source and test modules.

---

### Task 5: InteractiveSession Turn/Commit/Exit Controller

**Files:**
- Create: `src/coding_agent/interactive.py`
- Create: `tests/test_interactive.py`

**Interfaces:**
- Consumes: `AgentRunner.run_turn`, `ConversationHistory.copy`, `SessionRecord`, `redact_messages`, and store `save`.
- Produces: `InteractiveSession(runner, history, record, store, provider, model, sensitive_values, input_reader, output, result_sink)`.
- Produces: `InteractiveSession.run() -> int`, returning 0 for normal exit and 7 for persistence failure.
- Store contract used by controller: `save(record: SessionRecord) -> SessionRecord`.

- [ ] **Step 1: Write failing normal multi-turn tests**

Use FakeModelClient responses `first answer` and `second answer`, fake inputs `first task`, `follow-up`, `/exit`, an in-memory fake store, and captured output/results. Assert one canonical history contains both rounds, save occurs after each final response, final does not exit the loop, and the original unredacted in-memory history remains independent of redacted saved messages.

- [ ] **Step 2: Write failing input and interruption tests**

Cover blank input, whitespace/case-insensitive `/exit`, EOF, input-stage KeyboardInterrupt, and a runner stub that raises KeyboardInterrupt during a turn. Assert no model call for blank/exit, return code 0, and no save of the interrupted working copy.

- [ ] **Step 3: Write failing status and persistence-error tests**

Script MODEL_ERROR, MAX_STEPS, STALLED, INTERNAL_ERROR, then a later FINAL_RESPONSE. Assert the first three commit and continue, INTERNAL_ERROR does not commit its working copy and still permits another input, and a fake store `SESSION_SAVE_FAILED` causes return 7 with a concise message.

- [ ] **Step 4: Run Task 5 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-red" tests/test_interactive.py
```

Expected: import failure because `coding_agent.interactive` does not exist.

- [ ] **Step 5: Implement the synchronous controller**

Use standard `input_reader("you> ")`; strip only for command/blank detection while sending the original nonblank text to the model. For each turn, copy canonical history, call `run_turn`, route the RunResult to `result_sink`, and apply the status commit table verbatim from the Spec.

Before save, build a new `SessionRecord` whose messages are `redact_messages(working.persisted_messages, sensitive_values)`. Do not replace canonical history with redacted messages. Catch only EOFError and KeyboardInterrupt at the interaction boundary; let the existing runner produce protocol errors for ordinary model/tool failures.

- [ ] **Step 6: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-target" tests/test_interactive.py tests/test_agent.py tests/test_session_store.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-full"
```

Expected: controller, runner, store, and full-suite tests pass.

- [ ] **Step 7: Task review and checkpoint record**

Review commit table, canonical/working isolation, redaction-copy isolation, no accidental catch of normal errors, empty/exit semantics, persistence failure exit 7, and no session-management extras. Apply reviewed fixes and rerun tests. Record checkpoint suggestion: `src/coding_agent/interactive.py`, `tests/test_interactive.py`; message `feat: add deterministic interactive session loop`.

---

### Task 6: CLI Session Composition and Backward Compatibility

**Files:**
- Modify: `src/coding_agent/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_packaging.py` only if console-entry assertions require optional-task help updates.

**Interfaces:**
- Consumes: `resolve_session_home`, `JsonSessionStore`, `ConversationHistory.from_persisted`, `InteractiveSession`.
- Produces: optional `task` positional argument.
- Produces: mutually exclusive `--new-session` and `--resume-session SESSION_ID`.
- Produces: interactive initialization/selection and exit-code 7 mapping.
- Preserves: all existing one-shot flags, provider presets, secure key prompt, client factory injection, logging, and exit mappings.

- [ ] **Step 1: Write failing parser and mode-validation tests**

Assert help shows optional task and both session flags. Assert new/resume are mutually exclusive and either flag with a one-shot task returns 2 before model/store construction.

- [ ] **Step 2: Replace the old missing-task test with failing interactive composition tests**

Inject a temporary store factory and fake input containing `/exit`. Assert no task enters interactive mode, defaults to current workspace, automatically loads latest or creates a new record, prints the session ID, creates one client, and closes it once. Add explicit new, explicit resume, workspace mismatch, corrupt index, and provider/model-change warning cases.

- [ ] **Step 3: Add failing one-shot no-persistence regression test**

Run the existing documented task invocation with a store factory that fails if called. Assert existing factory arguments, RunStatus output, exit code, and key redaction remain unchanged.

- [ ] **Step 4: Run Task 6 tests and verify RED**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-red" tests/test_cli.py tests/test_packaging.py
```

Expected: no-task parsing or new session arguments fail because interactive CLI composition is absent.

- [ ] **Step 5: Implement minimal CLI composition**

Set task to `nargs="?"`. Add an argparse mutually exclusive group for the two session flags. Validate interactive-only use before config/client construction.

Factor client/runner construction enough to reuse one client in interactive mode without redesigning provider interfaces. Resolve session home from the same runtime environment mapping used for configuration. Select record by explicit new, explicit ID, or latest/default creation. Inject current SYSTEM_PROMPT on restore, print provider/model-change warnings, run InteractiveSession, and close the client in `finally`.

Keep one-shot `_run_agent` persistence-free and retain all existing exit codes. Catch SessionError only at CLI/session boundaries and print `[error] CODE: message` without traceback or key.

- [ ] **Step 6: Run targeted and full regression tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-target" tests/test_cli.py tests/test_interactive.py tests/test_packaging.py tests/test_end_to_end.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-full"
```

Expected: interactive CLI and all previous one-shot/end-to-end tests pass.

- [ ] **Step 7: Task review and checkpoint record**

Review client lifetime, no-task behavior, flag validation order, auto-resume, new/resume semantics, current workspace enforcement, warning-only provider/model change, one-shot no-write guarantee, exit codes, and key secrecy. Apply reviewed fixes and repeat tests. Record Task 5–6 checkpoint message `feat: add persistent interactive CLI sessions`.

---

### Task 7: Persistence Integration, Submission Docs, and Final Regression

**Files:**
- Create: `tests/test_interactive_end_to_end.py`
- Modify: `tests/test_readme.py`
- Modify: `README.txt`
- Modify: `demo/README.txt`
- Modify: implementation files from Tasks 1–6 only when an integration test proves a Spec gap.

**Interfaces:**
- Consumes: complete interactive CLI/session stack.
- Produces: offline disk-backed multi-process-equivalent resume evidence and submission-compliant documentation.

- [ ] **Step 1: Write failing disk-backed integration tests**

Use two sequential `main()` calls with the same temporary `CODING_AGENT_HOME` and workspace. First invocation creates a session, performs one FakeModel turn, then exits. Second invocation auto-resumes and performs a follow-up. Assert the second FakeModel receives the first user/final history plus the new user message and current system prompt.

Add a second integration test that creates two sessions, explicitly resumes the older ID, and proves another workspace cannot resume it. Assert serialized files contain neither the fake Provider key nor a system role.

- [ ] **Step 2: Run integration tests and verify RED if a cross-component gap remains**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-red" tests/test_interactive_end_to_end.py
```

Expected before integration fixes: at least one failing assertion exposes any remaining composition, history, metadata, or persistence mismatch. If all assertions pass immediately because Tasks 1–6 already satisfy the integration, record that this step is a characterization pass and do not invent production changes.

- [ ] **Step 3: Apply only integration-proven fixes**

Fix the smallest responsible component for each failing integration assertion. Do not add session list/delete/search, summaries, databases, model translation calls, or other excluded scope.

- [ ] **Step 4: Add failing README compliance assertions and update docs**

Extend `tests/test_readme.py` to require the compact interactive command, `/exit`, automatic workspace-local resume description, and plaintext local-session warning while retaining the 1000-character cap, repository URL, API Key, run method, and feature requirements.

Update `README.txt` with the shortest normal flow:

```text
进入目标项目目录，运行 python -m coding_agent --provider deepseek；缺少 API Key 时安全输入；/exit 或 Ctrl+C 退出；默认恢复该 workspace 最近 session。
```

Update `demo/README.txt` without changing the primary two-minute one-shot Demo. Add an optional follow-up turn as a secondary demonstration only.

- [ ] **Step 5: Run targeted integration, credential, and documentation tests**

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-target" tests/test_interactive_end_to_end.py tests/test_readme.py tests/test_cli.py tests/test_end_to_end.py
```

Expected: all integration, submission, credential-scan, CLI, and legacy end-to-end tests pass.

- [ ] **Step 6: Run final static and complete verification**

Run:

```powershell
python -m compileall -q src tests
python -m coding_agent --help
python -m pytest -q --basetemp "$env:TEMP\coding-agent-interactive-final"
```

Expected: compile/help exit 0 and full pytest output reports zero failures. Do not run a live Provider request; all approved behavior is offline-testable.

- [ ] **Step 7: Final task review and checkpoint record**

Review every Spec acceptance criterion, PDF constraints, README length, plaintext warning, one-shot Demo stability, credential scan, no excluded dependency, and actual test output. Apply necessary fixes and repeat final verification. Record final checkpoint suggestion covering integration tests and docs with message `docs: document persistent interactive sessions`.

## Plan Self-Review Result

- **Spec coverage — pass:** Tasks 1–7 cover all 11 success criteria, all 11 interactive/CLI acceptance paths, user-led context retention, schema-v1 validation, current-key redaction, session-first/index-second atomic replacement, stable session errors/exit code 7, documentation, and offline restart/resume evidence.
- **Architecture/interface consistency — pass:** dependencies flow one way from runner/history → session codec → store → interactive controller → CLI; every consumed interface is produced by an earlier task; `run()` remains the one-shot wrapper; `JsonSessionStore` never receives an API Key; `InteractiveSession` persists a redacted copy while retaining an unredacted in-process canonical history.
- **Placeholder check — pass:** there are no unresolved TODO/TBD/FIXME items or stub bodies. Occurrences of `...` are only Python variadic-tuple type syntax such as `tuple[Message, ...]`.
- **Scope check — pass:** the plan adds no provider, Agent Framework, dependency, database, summary, action journal, cross-workspace memory, session-management UI, Web UI, multi-Agent runtime, streaming, encryption claim, file lock, or process-tree termination work.
- **Implementation ruling:** the approved Design Spec governs implementation details. The first-turn assistant/tool tail is one user-led group after the permanent first-user anchor. A latest required group that cannot fit with anchors raises `ContextBudgetError`; no fallback drops the newest user request.
- **Execution ruling:** because the owner exclusively manages Git, subagent-driven development runs in the existing workspace without worktrees, commits, branch operations, or Git-derived review packages. Task briefs, task reports, scoped file inspection, test output, and the checkpoint records below replace those mechanics.

## Owner-Managed Checkpoint Summary Template

The controller records but does not execute these checkpoints:

1. Tasks 1–2: multi-turn history/runner/context; suggested message `refactor: support reusable agent conversation turns`.
2. Tasks 3–4: strict session codec and atomic JSON store; suggested message `feat: persist workspace sessions as atomic JSON`.
3. Tasks 5–6: interactive controller and CLI composition; suggested message `feat: add persistent interactive CLI sessions`.
4. Task 7: integration coverage and docs; suggested message `docs: document persistent interactive sessions`.

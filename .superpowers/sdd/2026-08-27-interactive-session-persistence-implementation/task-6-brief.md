### Task 6: CLI Session Composition and Backward Compatibility

**Files:**
- Modify `src/coding_agent/cli.py`
- Modify `tests/test_cli.py`
- Modify `tests/test_packaging.py` only if console-entry/help assertions require it

**Public behavior/interfaces:**

- positional `task` becomes optional (`nargs="?"`): present → existing one-shot mode; absent → persistent interactive mode;
- add mutually exclusive `--new-session` and `--resume-session SESSION_ID`;
- both session flags are invalid with a one-shot task and return argparse/config-style exit 2 before key prompt, store, or client construction;
- extend injectable composition with `session_store_factory: Callable[[Path], JsonSessionStore] = JsonSessionStore` and `input_reader: Callable[[str], str] = input` (equivalent precise aliases are allowed);
- interactive session initialization/save errors return 7;
- preserve all existing one-shot flags, presets, key prompt, provider factory, output, status/exit codes, six-tool composition, and key redaction.

#### Step 1: Add failing parser/mode tests

Replace the old “task required” test. Assert:

- help says task is optional/interactive when omitted and includes both flags;
- `--new-session` and `--resume-session` are argparse-mutually-exclusive;
- either flag plus task returns 2 and constructs/prompts nothing;
- raw `--api-key` rejection remains earlier and never echoes the value.

#### Step 2: Add failing interactive composition/selection tests

Use temporary storage, fake clock/IDs or an injected fake store, a closable FakeModelClient, injected input, and capsys. Cover:

1. no task defaults workspace to current directory; no latest record creates one in memory, prints `[session] created: <id>` and the exit hint, performs a turn then `/exit`, uses one client and closes once;
2. a later invocation with the same store root/default mode loads latest and prints `[session] resumed: <id>`;
3. `--new-session` creates another in-memory ID without deleting/overwriting older records and prints created;
4. `--resume-session <older-id>` restores that exact current-workspace record and prints resumed;
5. wrong workspace, unknown ID, corrupt index/session, unsupported version, and store I/O errors print `[error] <CODE>: <concise message>` without traceback/key, return 7, and do not construct a model client;
6. restoring persisted messages injects the current `SYSTEM_PROMPT`, never a persisted system message;
7. prior provider/model differing from current config prints warning-only messages, still runs, and the next successful save updates metadata;
8. provider API key is securely prompted at most once, one ModelClient is reused for all interactive turns, and it closes exactly once for normal exit, protocol statuses, and persistence exit 7.

Startup output remains:

```text
[run] workspace: <resolved workspace>
[run] provider: <provider>; model: <model>
[session] created|resumed: <12hex>
[session] enter /exit or press Ctrl+C to save and exit
```

Use existing event output for tool steps. Interactive RunResult rendering prints `[final] protocol status: <STATUS>` each turn; non-empty final text is emitted with `agent> ` rather than the one-shot `[response]` marker. Errors remain redacted and concise.

#### Step 3: Add failing one-shot no-persistence regression

Pass a `session_store_factory` that fails if called into an existing documented task invocation. Assert:

- no storage-home/store/session path is touched;
- current client factory arguments, event/final output, one-shot `[response]`, exit-code mapping, key prompt/redaction, and close behavior remain unchanged;
- all six tools and existing provider abstraction remain intact.

#### Step 4: Verify RED

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-red" tests/test_cli.py tests/test_packaging.py
```

Record failures caused by required task and absent session flags/composition.

#### Step 5: Minimal CLI composition

1. Parse and validate raw-key/session-mode arguments.
2. Resolve provider preset/key/config once using the current runtime environment mapping.
3. If task is present, call the existing persistence-free `_run_agent` path unchanged.
4. If task is absent, call `resolve_session_home(runtime_environment)`, construct the injected store, and select record: explicit new → create; explicit ID → strict load; default → strict latest load or create only when no index.
5. Restore history with current `SYSTEM_PROMPT` via `ConversationHistory.from_persisted`; new empty records use a system-only history.
6. Print provider/model-change warnings without rejecting. Construct exactly one client/runner/registry/context manager with current RuntimeConfig. Compose `InteractiveSession` with current provider/model, `(config.api_key,)`, injected input, stderr persistence-error output, and a redacting interactive result sink.
7. Close client in `finally`. Catch `SessionError` only at initialization/selection boundaries and return 7. Keep broad unexpected composition error behavior at 6 without traceback or key.

Do not place the API key in the store, persisted record, error, or session-home argument. Do not add a provider, session list/delete UI, in-session commands, summary/memory changes, streaming, prompt toolkit, database, or provider-interface redesign.

#### Step 6: Verify GREEN and regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-target" tests/test_cli.py tests/test_interactive.py tests/test_packaging.py tests/test_end_to_end.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-full"
```

#### Step 7: Self-review/report

Review validation order, store/client construction lifetime, auto-resume/new/explicit resume, strict workspace enforcement, warning-only metadata changes, current system injection, one-shot zero persistence, output markers/exit codes/key secrecy, and excluded scope.

No Git operations. Write full RED/GREEN evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-6-report.md`.

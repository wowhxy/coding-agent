### Task 5: InteractiveSession Turn/Commit/Exit Controller

**Files:**
- Create `src/coding_agent/interactive.py`
- Create `tests/test_interactive.py`

**Interface:**

Create a synchronous `InteractiveSession` whose constructor receives:

- `runner: AgentRunner`
- `history: ConversationHistory` (the current committed canonical state)
- `record: SessionRecord`
- `store` with `save(record: SessionRecord) -> SessionRecord`
- current `provider: str` and `model: str`
- `sensitive_values: tuple[str, ...]`
- injectable `input_reader: Callable[[str], str]` (default standard `input`)
- injectable `output: Callable[[str], None]` (default `print`)
- injectable `result_sink: Callable[[RunResult], None]`

Produce `InteractiveSession.run() -> int`: 0 for normal `/exit`/EOF/Ctrl+C and 7 for persistence failure. Keep current canonical history/record accessible to CLI/tests through stable instance attributes.

#### Step 1: Add failing normal multi-turn/transaction tests

Use one `FakeModelClient`/real `AgentRunner`, fake inputs `first task`, `follow-up`, `/exit`, an in-memory fake store, captured output/results, and an initial empty `SessionRecord`. Prove:

- both user turns run on one canonical history and both final assistant messages remain;
- `FINAL_RESPONSE` commits/saves and returns to input rather than exiting the process;
- save occurs after each final response and returned store metadata becomes current record;
- save receives current provider/model and only `working.persisted_messages` (never system);
- a fake known provider key appearing in a model/tool message is `[REDACTED]` in the saved record;
- the accepted in-process canonical history retains its original unredacted content and is not aliased to saved messages;
- original nonblank input, including meaningful leading/trailing whitespace, is passed to `run_turn`; stripped input is only for blank/command detection.

#### Step 2: Add failing input/interruption tests

Cover blank input, whitespace/case-insensitive exact `/exit`, a non-exact `/exit now` ordinary task, EOFError, KeyboardInterrupt while awaiting input, and a runner stub raising KeyboardInterrupt after mutating its working copy. Assert blank/exit never call model, all normal exits return 0, and an interrupted current working copy is neither saved nor installed as canonical.

#### Step 3: Add failing RunStatus/persistence tests

Using deterministic runner stubs, cover the approved commit table:

| RunStatus | Commit working history? | Continue input? |
|---|---:|---:|
| `FINAL_RESPONSE` | yes | yes |
| `MODEL_ERROR` | yes | yes |
| `MAX_STEPS` | yes | yes |
| `STALLED` | yes | yes |
| `INTERNAL_ERROR` | no | yes |

Prove each result is sent once to `result_sink`. For committed non-final statuses, valid user/tool history is saved even without final assistant text. For INTERNAL_ERROR discard all current-turn mutations and accept the next input from the prior canonical state.

Make a fake store raise each persistence `SessionError`; `run()` must output one concise `[error] <CODE>: <message>` line, return 7, leave canonical/record at the previous committed state, and stop taking input. Do not expose a sensitive value.

#### Step 4: Verify RED

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-red" tests/test_interactive.py
```

Expected initial failure: module absent. Record it.

#### Step 5: Minimal implementation

Loop synchronously on `input_reader("you> ")`. Use `stripped = raw.strip()` only for blank and `stripped.casefold() == "/exit"`. For a task:

```text
working = canonical.copy()
result = runner.run_turn(working, raw)
result_sink(result)
apply commit table
if committing: redact a new persisted tuple → replace record messages/provider/model → store.save → only then install returned record and working canonical
```

Catch EOFError/KeyboardInterrupt at the input boundary as normal exit. Catch KeyboardInterrupt around `run_turn` as abandon-working-copy and normal exit. Catch `SessionError` only around persistence, render the stable code/message through output, and return 7. Do not catch ordinary runner protocol outcomes; `AgentRunner` already maps model/tool/internal failures to RunResult.

Do not mutate canonical on failed save, do not replace canonical with redacted messages, do not create an empty session file, and do not add CLI selection, `/new`, `/resume`, UI, summary, streaming, or provider behavior.

#### Step 6: Verify GREEN and regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-target" tests/test_interactive.py tests/test_agent.py tests/test_session_store.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-full"
```

#### Step 7: Self-review/report

Review exact commit table, canonical/working/save-copy isolation, interruption stages, empty/exit semantics, result routing, persistence error 7, key secrecy, and excluded scope.

No Git operations. Write full RED/GREEN evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-5-report.md`.

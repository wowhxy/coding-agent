# Task 5: InteractiveSession Turn/Commit/Exit Controller Report

## Scope and files

- Created `src/coding_agent/interactive.py` with the synchronous `InteractiveSession` controller and its narrow persistence protocol.
- Created `tests/test_interactive.py` with deterministic, offline controller tests using `FakeModelClient`, `AgentRunner`, scripted runner stubs, fake stores, injected input, and captured output/results.
- No CLI, session-selection, store, provider, streaming, or UI behavior was changed. No Git operations were performed.

## RED evidence

Before production code existed, the task-specific test suite was run with:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-red" tests/test_interactive.py
```

Result: collection failed as expected with `ModuleNotFoundError: No module named 'coding_agent.interactive'` (1 collection error, 0.27s).

## Implementation

`InteractiveSession.run()` loops synchronously on `input_reader("you> ")`. It retains raw nonblank task input, using only `raw.strip()` for blank detection and exact case-insensitive `/exit` recognition. Each task runs against `history.copy()`.

`FINAL_RESPONSE`, `MODEL_ERROR`, `MAX_STEPS`, and `STALLED` commit; `INTERNAL_ERROR` continues without committing; `KeyboardInterrupt` during a turn abandons the working copy and exits normally. A commit creates a separately redacted persisted-message tuple, replaces record provider/model with current runtime values, saves it, and only then installs the returned record and unredacted working history. `SessionError` produces one redacted `[error] CODE: message` line, returns 7, and preserves prior canonical state.

## GREEN and regression evidence

Targeted command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-target" tests/test_interactive.py tests/test_agent.py tests/test_session_store.py
```

Result: `70 passed in 0.55s`.

Full command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task5-full"
```

Result: `292 passed, 5 skipped in 8.59s`.

## Self-review

- Commit table exactly accepts `FINAL_RESPONSE`, `MODEL_ERROR`, `MAX_STEPS`, and `STALLED`; `INTERNAL_ERROR` is discarded.
- Canonical history is copied per turn and is installed only after a successful save; the saved message tuple is a redacted copy and excludes the system message.
- Input-boundary EOF/Ctrl+C, run-time Ctrl+C, blank input, exact `/exit`, and non-exact `/exit now` have dedicated coverage.
- Each delivered `RunResult` reaches `result_sink` once; normal protocol outcomes continue input processing.
- Persistence failure stops further input, returns 7, preserves prior history/record, and redacts configured sensitive values from the emitted error.
- Fresh sessions are not saved on blank input or immediate exit, and excluded CLI/session-management scope was not added.

## Concerns

None. The controller intentionally leaves normal result rendering to the injected `result_sink`; CLI composition remains a later task.

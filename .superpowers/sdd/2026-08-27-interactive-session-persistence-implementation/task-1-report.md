# Task 1 Report: Copyable Conversation History and One-Turn AgentRunner

## Implementation summary

- `ConversationHistory` now supports a system-only construction, optional original task, persisted-message recovery, immutable non-system snapshots, and independent copies.
- `AgentRunner.run_turn()` appends the user message, runs the existing bounded loop with per-call step and failure state, and stores a final assistant message before returning `FINAL_RESPONSE`.
- `AgentRunner.run()` remains compatible with the one-shot interface by creating a system-only history and delegating once.
- The exact latest-user-message language policy was added to `SYSTEM_PROMPT` without CLI behavior.

## TDD evidence

### RED

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-red" tests/test_context.py tests/test_agent.py tests/test_config.py
```

Output summary:

```text
10 failed, 55 passed in 0.35s
```

Expected relevant failures were observed: the history constructor required an original task; `from_persisted`, `copy`, and `persisted_messages` were missing; `AgentRunner.run_turn` was missing; and the exact response-language policy was absent.

### GREEN: targeted

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-target" tests/test_context.py tests/test_agent.py tests/test_config.py
```

Output:

```text
65 passed in 0.25s
```

### GREEN: full regression

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-full"
```

Output:

```text
196 passed, 5 skipped in 7.49s
```

## Files changed

- `src/coding_agent/context.py`
- `src/coding_agent/agent.py`
- `src/coding_agent/system_prompt.py`
- `tests/test_context.py`
- `tests/test_agent.py`
- `tests/test_config.py`
- `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-1-report.md`

## Self-review

- Mutation isolation: `copy()` constructs a new list, and the regression test appends only to the copy.
- Persisted-message validation: recovery rejects an empty tuple, a non-user first message, and any system message; the restored system prompt is always the supplied current prompt.
- One-shot compatibility: existing `run()` tests remain green through single delegation to `run_turn()`.
- Final-message retention: the final model text is appended as an assistant `Message` before the final result is returned.
- Per-turn reset: `step`, repeated-failure fingerprint, and consecutive-failure count are initialized inside `run_turn()`; the one-step test executes two separate turns.
- Response-language rule: the exact required sentence is present in the system prompt.
- Scope: no session store/persistence implementation or context regrouping changes were made.

## Concerns

None. Context grouping remains intentionally unchanged for the later Task 2 implementation.

# Final consolidated fix wave report

## Status and scope

Completed the exact final-fix brief without Git, worktree, commit, subagent, network, live Provider, real API key, or real user session storage access. All filesystem behavior in tests uses pytest temporary paths and injected fake model clients.

The implementation changes are limited to the concrete JSON session-store boundary, strict session/index JSON parsing, the context error docstring, README exit wording, and focused regression coverage. No deferred zero-save assertions were added.

## Files changed

- `src/coding_agent/session_store.py`
- `src/coding_agent/session.py`
- `src/coding_agent/context.py`
- `README.txt`
- `tests/test_session_store.py`
- `tests/test_session.py`
- `tests/test_context.py`
- `tests/test_interactive.py`
- `tests/test_cli.py`
- `tests/test_readme.py`
- this report

The existing CLI fixtures that had placed their test session homes inside their active temporary workspaces were adjusted to use a sibling temporary session home. This is fixture alignment with the new security invariant, not a product behavior expansion.

## TDD evidence

### RED before production edits

After adding the focused tests and before production edits, ran:

```powershell
python -m pytest tests/test_session_store.py tests/test_session.py tests/test_context.py tests/test_interactive.py tests/test_cli.py tests/test_readme.py -q
```

Result: exit 1; `19 failed, 164 passed, 1 skipped in 2.17s`.

The expected RED failures proved these missing behaviors:

- `JsonSessionStore` accepted a root equal to or nested beneath the resolved workspace for create, both load paths, and save; CLI then returned 0 rather than session exit 7.
- A valid record whose `session_id` differed from its requested filename was accepted by both explicit and latest loading.
- Session and index parsing accepted `NaN`, `Infinity`, and `-Infinity` when a later duplicate schema-version key was valid.
- Index replacement after a successful session replacement was reported as the generic `session could not be saved` message rather than a partial-success message.
- README still said `/exit` or `Ctrl+C` saves and exits.

The direct missing-schema-version test, positive first-turn tool-tail test, committed non-final tool-pair test, and external-root/contained-workspace test were legitimate characterization passes: their existing behavior already met the approved design, so no unrelated production change was invented.

### GREEN implementation

Implemented the smallest responsible changes:

- Canonicalize the storage root with `Path.resolve(strict=False)` on construction and immediately before every create/load/save boundary. Reject a root equal to or contained by the resolved workspace with existing `SESSION_IO_ERROR` and the concise message `session storage root must be outside workspace`.
- Bind `sessions/<requested_id>.json` to `record.session_id` after deserialization; mismatches now raise concise `SESSION_CORRUPT`.
- Preserve session-first/index-second atomic replacement, but give the index-write boundary its own `SESSION_SAVE_FAILED` message: `session was saved but workspace index was not updated`. Session-replacement failures retain `session could not be saved`.
- Use `json.loads(..., parse_constant=...)` in both codecs to reject all non-standard constants, mapping session input to `SESSION_CORRUPT` and index input to `SESSION_INDEX_CORRUPT`. Removed redundant `JSONDecodeError`/`ValueError` catch lists.
- Updated `ContextBudgetError` wording and README exit semantics exactly within scope.

The first post-fix affected run exposed eight old CLI fixtures using a storage root inside their own test workspace. The new store correctly rejected them. I moved only those fixture roots to sibling temporary paths, then reran the exact affected CLI tests: `8 passed in 0.55s`.

## Final verification

### Focused affected tests

```powershell
python -m pytest tests/test_session_store.py tests/test_session.py tests/test_context.py tests/test_interactive.py tests/test_cli.py tests/test_readme.py tests/test_interactive_end_to_end.py -q
```

Exit 0: `185 passed, 1 skipped in 1.87s`.

The one skip is the new Windows directory-symlink regression when the environment does not grant symlink creation. The test is present; it verifies an existing symlink component resolves inside the workspace before containment is checked. The six-skip full suite includes this plus the five pre-existing platform/optional-tool skips recorded by Task 7.

### Task 7 targeted suite

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-final-fix-task7-20260828" tests/test_interactive_end_to_end.py tests/test_readme.py tests/test_cli.py tests/test_end_to_end.py
```

Exit 0: `51 passed in 1.18s`.

### Static compilation

```powershell
python -m compileall -q src tests
```

Exit 0 with no output.

### CLI help

```powershell
python -m coding_agent --help
```

Exit 0. Help continues to show no-task interactive mode, `--new-session`, `--resume-session`, workspace selection, and all existing configuration flags.

### Fresh full suite

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-final-fix-full-review-20260828"
```

Exit 0: `342 passed, 6 skipped in 9.36s`.

## Self-review

- Session root is checked at `JsonSessionStore` rather than only in CLI, before any session path inspection, index read, or atomic write. A workspace beneath an external storage root remains accepted.
- CLI selection hits the concrete store before client construction; the direct CLI regression verifies exit 7, no client factory call, and no storage artifact for `CODING_AGENT_HOME=<workspace/subdir>`.
- Explicit and latest index-selected loads reject record/filename ID disagreement without rename or recovery.
- Session/index write ordering remains session then index; no cross-file rollback is claimed. Tests prove session-write failure preserves its original message and index-write failure retains the readable new session plus old index bytes.
- Strict JSON constants are rejected even when a later duplicate key would otherwise overwrite them; errors stay concise and contain no supplied document/key content.
- The missing schema-version classification is directly tested as `SESSION_CORRUPT`.
- The first-turn assistant/tool tail, a committed `MAX_STEPS` tool-call/tool-result pair, and required context-error wording are covered without changing already-correct context or interactive behavior.
- README is 865 characters, preserves the required second-line repository URL, and now distinguishes normal `/exit`/input-stage interrupt behavior from run-stage interruption and zero-commit new sessions.
- No source/test additions assert deferred blank/exit/interruption fake-store zero saves, and no unrelated feature was added.

## Concerns

No known repository-local implementation or regression concern remains. The only limitation is the environment-level Windows symlink-permission skip described above; all non-symlink security cases and the concrete canonicalization code execute and pass. External Git/submission artifacts remain owner-managed and were not inspected.

## Git record

Commits: none. No Git command or operation was run.

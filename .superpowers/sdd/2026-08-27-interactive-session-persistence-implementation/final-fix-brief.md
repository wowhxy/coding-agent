# Final consolidated fix wave

This is the only final-review fix wave. Modify only the smallest responsible files and use TDD/focused regression for every behavior.

## Important 1 — session root must stay outside active workspace

The approved design says persisted sessions live outside the workspace so local file tools cannot access them. Canonicalize the session root (including existing symlink components) and reject any root equal to or contained by the resolved workspace before creating/loading/saving files. Enforce at the concrete `JsonSessionStore` boundary so non-CLI callers cannot bypass it; CLI using that store must return concise session error exit 7 with no client and no storage artifact. Use an existing stable session I/O/config error code rather than adding a new public code. Test direct store create/load/save as relevant and `CODING_AGENT_HOME=<workspace/subdir>` CLI behavior. A workspace contained beneath an external session root is not the forbidden direction.

## Important 2 — bind session filename to record ID

After deserializing `sessions/<requested_id>.json`, require `record.session_id == requested_id`; otherwise raise concise `SESSION_CORRUPT`. Cover explicit `load_session` and `load_latest` where an index points at a filename containing another valid record. Do not rename or silently accept it.

## Important 3 — distinguish partial index-save recovery

Keep session-first/index-second individual atomic replacement and keep error code `SESSION_SAVE_FAILED`. Split error boundaries so:

- session replacement failure retains the existing concise session-save failure message;
- index replacement failure after session success raises a concise message explicitly stating the session was saved but the workspace index was not updated.

Add direct store assertions and CLI/controller output assertion for the partial-success message; do not claim cross-file transaction rollback.

## Strict JSON and codec polish

- Reject non-standard JSON constants `NaN`, `Infinity`, and `-Infinity` as malformed input in both session and workspace-index parsing using strict `json.loads` constant handling.
- Session error is `SESSION_CORRUPT`; index error is `SESSION_INDEX_CORRUPT`.
- Add direct missing-`schema_version` session test.
- Remove redundant `JSONDecodeError`/`ValueError` catch structure while preserving all prior classifications and fixed error secrecy.

## Context and interactive regression precision

- Update `ContextBudgetError` docstring to cover permanent-anchor or required-latest-turn overflow.
- Add a positive test proving the first-turn assistant/tool tail after the first-user anchor remains selectable/retained when `recent_turns` and budget allow.
- Add an interactive committed non-final status test whose valid working history contains an assistant tool call and its paired tool result; assert the persisted record keeps the complete pair.
- The direct fake-store zero-save assertions for blank/exit/interruption remain deferred; do not expand this wave for them.

## README accuracy

Replace the imprecise statement that `/exit` or `Ctrl+C` always “saves and exits.” State compactly that `/exit`/input-stage Ctrl+C exits normally, while run-stage Ctrl+C discards the unfinished current turn; a new session without a committed turn leaves no session file. Keep `README.txt` at most 1000 characters and all existing required assertions/second-line URL.

## Verification and report

Use tests-first for all new behaviors. Run focused affected tests, Task 7 targeted tests, `python -m compileall -q src tests`, `python -m coding_agent --help`, and the full suite with fresh basetemps. Do not use real API/network/user storage.

No Git operations. Write RED/GREEN commands/results, files, self-review and concerns to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/final-fix-report.md`.

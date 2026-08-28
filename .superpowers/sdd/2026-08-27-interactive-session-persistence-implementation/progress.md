# SDD ledger — plan: docs/superpowers/plans/2026-08-27-interactive-session-persistence-implementation.md

Spec: `docs/superpowers/specs/2026-08-27-interactive-session-persistence-design.md` (approved)

## Preflight rulings

- Ruling: execute in the current workspace without a worktree, commits, branch operations, or Git-derived diff packages — the owner explicitly reserves every Git write and asked execution to continue — if wrong, task isolation and review must rely on scoped files/reports rather than commit ranges.
- Ruling: reviewers receive a task-scoped review manifest plus the brief/report and inspect only named current files — dubious-ownership Git is owner-managed and must not be debugged — if wrong, review loses automatic before/after hunks but retains source-level and test-evidence inspection.
- Ruling: the first session's assistant/tool tail is treated as its own selectable group after the permanent first-user anchor, while every later user starts a new group — this directly follows Design Spec section 6 — if wrong, `recent_turns` counting could retain one fewer older follow-up.
- Ruling: Task 7's new cross-component integration test may be green on its first run when Tasks 1–6 already meet it; this is recorded as characterization and no artificial production defect is introduced — the plan explicitly requires this — if wrong, that integration check will lack a RED implementation transition, while Task 7 documentation assertions still use RED/GREEN.

## Preflight consistency matrix

| Tasks | Produced / consumed surface | Finding |
|---|---|---|
| 1 ↔ 2 | Task 1 produces copyable/restorable history; Task 2 consumes its message shape | Consistent; system + optional first user supports anchors and later user-led turns. |
| 1 ↔ 5 | Task 1 produces `run_turn`, copy, persisted messages; Task 5 consumes them transactionally | Consistent; working-copy mutation is isolated until commit. |
| 1 ↔ 6 | Task 1 preserves `run()`; Task 6 preserves one-shot CLI | Consistent; one-shot remains a wrapper and never enters storage composition. |
| 1 ↔ 7 | Task 1 retains final assistant messages; Task 7 verifies resumed history | Consistent; persisted transcript includes protocol-final text. |
| 2 ↔ 7 | Task 2 produces bounded user-led context; Task 7 exercises restored multi-turn context | Consistent; full disk history is bounded only at model request time. |
| 3 ↔ 4 | Task 3 produces strict codec/errors; Task 4 consumes them at storage boundaries | Consistent; codec owns session schema, store owns paths/index/I/O mapping. |
| 3 ↔ 5 | Task 3 produces record/redaction; Task 5 persists a redacted copy | Consistent; canonical in-memory history remains unredacted. |
| 3 ↔ 7 | Task 3 excludes system/key; Task 7 scans disk records | Consistent; current system prompt is injected on restore. |
| 4 ↔ 5 | Task 4 produces `save -> SessionRecord`; Task 5 commits returned metadata | Consistent; session write precedes index write. |
| 4 ↔ 6 | Task 4 produces home/create/load APIs; Task 6 performs selection | Consistent; selection errors are not silently replaced. |
| 4 ↔ 7 | Task 4 supports multiple sessions/workspaces; Task 7 verifies restart/resume | Consistent; tests use an injected temporary root. |
| 5 ↔ 6 | Task 5 produces synchronous interactive controller; Task 6 composes it | Consistent; one client and one active session per process. |
| 5 ↔ 7 | Task 5 commits by RunStatus; Task 7 verifies multi-turn persistence | Consistent; FINAL_RESPONSE ends a turn, not the process. |
| 6 ↔ 7 | Task 6 produces CLI flags/modes; Task 7 documents and integrates them | Consistent; the primary Demo remains one-shot. |
| Task 1 internal | lifecycle tests vs history/runner/prompt changes | Consistent; exact required interfaces and per-turn resets are testable offline. |
| Task 2 internal | grouping/budget tests vs helper and selection algorithm | Consistent under the first-tail ruling above. |
| Task 3 internal | strict round-trip/corruption tests vs codec grammar | Consistent; no store responsibilities are pulled into the codec. |
| Task 4 internal | home/index/failure tests vs atomic store | Consistent; no false cross-file transaction claim. |
| Task 5 internal | input/status/error tests vs commit table | Consistent; INTERNAL_ERROR and interruption discard the working copy. |
| Task 6 internal | parser/composition/regression tests vs CLI wiring | Consistent; session flags are interactive-only and one-shot remains storage-free. |
| Task 7 internal | restart integration/docs/static/full checks | Consistent; integration may characterize already-complete behavior per ruling. |

## Progress

Task 1: complete (owner-managed files; independent review clean)
Task 1 checkpoint: `src/coding_agent/context.py`, `src/coding_agent/agent.py`, `src/coding_agent/system_prompt.py`, `tests/test_context.py`, `tests/test_agent.py`, `tests/test_config.py`; suggested message `refactor: support reusable agent conversation turns`
Task 2: in progress
Task 2: minor (deferred): `ContextBudgetError` docstring still names only anchor overflow although required-latest overflow is also supported.
Task 2: minor (deferred): first-turn-tail retention is implemented but lacks one direct positive-retention test.
Task 2: complete (owner-managed files; independent review approved with 2 deferred minors)
Checkpoint 1: Tasks 1–2; suggested files `src/coding_agent/context.py`, `src/coding_agent/agent.py`, `src/coding_agent/system_prompt.py`, `tests/test_context.py`, `tests/test_agent.py`, `tests/test_config.py`; suggested message `refactor: support reusable agent conversation turns`
Task 3: in progress
Task 3: minor (deferred): Python `json.loads` permissively accepts NaN/Infinity; strict JSON rejection can be considered in final review.
Task 3: fix round 1/5 started — enforce user transition after final assistant; classify unsupported numeric schema before v1 exact-root validation.
Task 3: fix round 1/5 (2 addressed, 0 open; owner-managed files)
Task 3: complete (owner-managed files; re-review clean; 2 deferred minors)
Task 4: in progress
Task 4: fix round 1/5 started — map invalid UTF-8 to document-specific corruption codes; bound repeated ID collisions and end with `SESSION_SAVE_FAILED`.
Task 4: fix round 1/5 (2 implementation findings addressed, 1 report-evidence labeling issue open; owner-managed files)
Task 4: fix round 2/5 started — make the earlier abbreviated first-GREEN excerpt explicit and clarify that fix-round RED evidence is complete.
Task 4: fix round 2/5 (report finding addressed, 0 open; owner-managed report)
Task 4: complete (owner-managed files; re-review clean)
Checkpoint 2: Tasks 3–4; suggested files `src/coding_agent/session.py`, `src/coding_agent/session_store.py`, `tests/test_session.py`, `tests/test_session_store.py`; suggested message `feat: persist workspace sessions as atomic JSON`
Task 5: in progress
Task 5: minor (deferred): committed non-final status tests do not include a tool-history example.
Task 5: minor (deferred): interrupted-turn test proves canonical rollback but does not directly retain/assert fake-store zero saves.
Task 5: minor (deferred): blank/exit tests prove zero runner calls but not explicit zero saves.
Task 5: complete (owner-managed files; independent review approved with 3 deferred test-only minors)
Task 6: in progress
Task 6: fix round 1/5 started — reject API-key environment names that collide with non-secret runtime/storage environment variables before prompt/config/store/client work.
Task 6: fix round 1/5 (security finding addressed, 0 open; owner-managed files)
Task 6: complete (owner-managed files; re-review clean)
Checkpoint 3: Tasks 5–6; suggested files `src/coding_agent/interactive.py`, `src/coding_agent/cli.py`, `tests/test_interactive.py`, `tests/test_cli.py`; suggested message `feat: add persistent interactive CLI sessions`
Task 7: in progress
Task 7: complete (owner-managed files; independent review clean)
Checkpoint 4: Task 7; suggested files `tests/test_interactive_end_to_end.py`, `tests/test_readme.py`, `README.txt`, `demo/README.txt`; suggested message `docs: document persistent interactive sessions`
Final review: in progress
Final review: With fixes — 3 Important findings (workspace-contained session root; filename/record ID mismatch; ambiguous partial index failure), strict-JSON/README minors, and four deferred items triaged fix-now.
Final fix wave: in progress (one consolidated wave per SDD final-review rule)

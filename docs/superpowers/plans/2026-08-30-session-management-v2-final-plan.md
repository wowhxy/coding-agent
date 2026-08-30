# Session Management v2 Final Implementation Plan

> **For agentic workers:** execute inline task-by-task with TDD. Git writes are
> owner-managed and forbidden to the implementing agent.

**Goal:** Make latest restore, session navigation, search, and recall scale with
the selected page/results rather than total canonical history.

**Architecture:** Add an atomic latest pointer and a rebuildable per-workspace
SQLite catalog/contentless-FTS index. Keep canonical JSON authoritative and
materialize only FTS-matched sessions.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` FTS5, JSON, Textual, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-session-management-v2-final-design.md`

## Global constraints

- Do not change canonical session schema or truncate/rewrite history.
- Derived-state failure must not invalidate a successful canonical save.
- No new third-party search, persistence, tokenizer, agent, or UI dependency.
- Preserve one-shot/interactive/TUI, Recall, Memory, Skills, Plugins, Subagents,
  parallel tools, and current `/new` semantics.

### Task 1: Latest pointer and lightweight catalog

**Files:** create `src/coding_agent/session_index.py`; modify
`src/coding_agent/session_store.py`; add `tests/test_session_index.py` and extend
`tests/test_session_store.py`.

**Interfaces:** `SessionIndex.ensure/rebuild/upsert/remove/list/get/latest` and
`JsonSessionStore.list_sessions(workspace, *, limit=None, offset=0)`.

- [ ] Add failing tests for valid/missing/corrupt/dangling latest pointers,
  zero/one/many sessions, restart, paging/order/dedup, and zero history loads.
- [ ] Implement the atomic pointer, catalog schema, lazy migration/rebuild, and
  operation report with the minimum code needed for those tests.
- [ ] Run targeted store/index tests, then the full suite; perform one concise
  batch review and fix ordinary findings once.

### Task 2: Shared locator FTS and bounded projections

**Files:** extend `src/coding_agent/session_index.py`; create
`src/coding_agent/session_search.py`; add `tests/test_session_search.py`.

**Interfaces:** `SearchLocator`, `SessionSearchResult`,
`SessionSearchService.search(workspace, query, limit=20,
exclude_session_id=None, fts_enabled=True)`.

- [ ] Add failing tests for English/Unicode/Chinese/mixed/path/code queries,
  BM25/recency ordering, bounded Top-K/snippets, secret and huge-tool exclusion,
  and one canonical load per matched session.
- [ ] Implement contentless FTS5 with locator rows, deterministic normalization,
  canonical materialization, and a workspace-scoped scan fallback.
- [ ] Test new/append/rename/delete index updates plus missing/corrupt/schema/
  FTS-unavailable recovery; run targeted and full tests, then one concise review.

### Task 3: Canonical-first lifecycle and migration safety

**Files:** modify `src/coding_agent/session_store.py`, `src/coding_agent/session_index.py`,
and existing persistence/discovery tests.

- [ ] Add failing tests proving canonical save succeeds when derived updates fail,
  stale state rebuilds, rename removes old-title hits, deleting latest repairs the
  pointer, deleting the DB does not affect resume, and migration is idempotent.
- [ ] Integrate save/rename/delete in canonical -> catalog/FTS -> pointer order;
  keep explicit canonical load errors strict while derived recovery skips only
  malformed individual files.
- [ ] Run persistence/search targeted tests and full regression; perform one
  concise lifecycle review and necessary fixes.

### Task 4: Recall and TUI integration

**Files:** modify `src/coding_agent/recall.py`, `src/coding_agent/application/service.py`,
`src/coding_agent/application/state.py`, `src/coding_agent/tui/widgets.py`,
`src/coding_agent/tui/app.py`, TUI fakes, and Recall/TUI tests.

- [ ] Add failing tests that Recall and TUI share locator search, sidebar startup
  reads a catalog page only, typing a filter invokes FTS, opening a hit loads only
  that session, and new/switch/rename/delete still work at large counts.
- [ ] Replace Recall's duplicate fingerprint/index implementation with the shared
  service; add a 50-row TUI catalog page and bounded search results without
  changing command or context-menu semantics.
- [ ] Run Recall/application/TUI targeted tests and full regression; perform one
  concise integration review and necessary fixes.

### Task 5: Benchmark, E2E, documentation, and final verification

**Files:** create `scripts/benchmark_sessions.py` and
`tests/test_session_management_v2_e2e.py`; update `README.md` only where user-facing
session search/navigation behavior needs documentation.

- [ ] Add deterministic E2E covering many sessions, restart latest, catalog list,
  FTS search/open, rename/delete, shared Recall, DB deletion/rebuild, exact history
  preservation, workspace isolation, and observability assertions.
- [ ] Benchmark 100 and 1000 sessions for legacy-equivalent vs pointer latest,
  history list vs catalog list, and scan vs FTS search; report N, canonical bytes,
  index bytes, and elapsed times without CI millisecond thresholds.
- [ ] Run targeted tests, TUI regression, full pytest, compile/syntax, CLI smoke,
  dependency/credential scans, and one final concise review. Report only measured
  evidence and declare FINAL only if every acceptance item passes.

## Self-review

- Spec coverage: latest/catalog/search/Recall/TUI/lifecycle/migration/recovery/
  observability/benchmark/E2E/security/isolation are assigned above.
- Interface consistency: catalog returns metadata; search returns locators/results;
  only explicit load/open returns canonical records.
- Placeholder scan: every task names concrete files, interfaces, tests, and commands.
- Scope: no change to canonical schema, agent runtime, Context/Memory, or excluded
  session/search features.

# Coding Agent v1 Final Hardening and Benchmark Plan

> **For agentic workers:** execute inline and sequentially. Do not use multi-reviewer SDD. Git writes remain owner-managed.

**Goal:** Audit the complete Coding Agent, measure it with bounded offline benchmarks, repair only evidence-backed P0/P1/high-value P2 defects, and decide whether v1 can be frozen.

**Architecture:** Canonical runtime behavior remains unchanged unless a failing test or benchmark demonstrates a defect. A separate `benchmarks` package owns disposable fixtures, timing/memory measurement, and JSON reports; correctness assertions remain in pytest. Existing subsystem APIs are exercised directly so the benchmark measures the real implementation rather than a duplicate harness.

**Tech stack:** Python 3.11+, stdlib `tempfile`/`time`/`tracemalloc`/`sqlite3`, existing `FakeModelClient`, pytest, Textual test pilot, existing production classes.

**Spec:** User-approved `FINAL HARDENING + ARCHITECTURE AUDIT + BENCHMARK + REPAIR` request (2026-08-30), constrained by all approved subsystem specs under `docs/superpowers/specs/`.

## Global constraints

- Measure before optimizing; correctness before performance.
- No new product feature, provider, framework/SDK, dependency, or architecture rewrite.
- Benchmarks are repeatable, bounded, non-destructive, mostly offline, and machine-readable; pytest contains no fragile wall-clock threshold.
- Every production repair starts with a failing regression test and ends with targeted tests plus the affected benchmark.
- Canonical history/session JSON remains source of truth; Context and SQLite FTS/catalog remain derived views.
- Never persist or print credentials/private histories. Git writes are forbidden.

## File map

- Create `benchmarks/__init__.py`: benchmark package marker.
- Create `benchmarks/common.py`: environment metadata, bounded timing/peak-memory measurement, JSON-safe results, disposable-root helpers.
- Create `benchmarks/scenarios.py`: benchmark A-I implementations against production APIs.
- Create `benchmarks/coding_tasks.py`: disposable deterministic coding task fixtures and verification for benchmark J.
- Create `benchmarks/run.py`: CLI profile selection and atomic JSON output.
- Create `tests/test_benchmark_suite.py`: schema, boundedness, cleanup, and smoke correctness (not speed thresholds).
- Create `tests/test_final_hardening.py`: only evidence-backed failure-injection/stress regressions.
- Create `benchmark_results/baseline.json` and `benchmark_results/final.json`: sanitized measurements.
- Create `docs/final-hardening-report.md`: architecture map, audit matrix, before/after evidence, 27-gate verdict, limitations.
- Modify production files only where a failing regression proves a P0/P1/high-value P2 defect.

### Task 1: Baseline and whole-architecture audit

**Evidence:** `python -m pytest -q`, `python -m compileall -q src tests scripts`, CLI help; inspect exception ownership, lifecycle, cancellation, canonical/derived state, trust boundaries, LLM calls, and large-object copies.

**Deliverable:** Record the current architecture map and an audit issue table with severity, evidence, owner, and disposition. Do not modify runtime code.

### Task 2: Benchmark infrastructure and A-D scenarios

**Interfaces:** `measure(name: str, operation: Callable[[], dict[str, object]]) -> dict[str, object]`; `run_scenarios(profile: str) -> list[dict[str, object]]`; each result includes `scenario`, `status`, `elapsed_seconds`, `peak_bytes`, and scenario metrics.

**TDD cycle:** First add smoke tests proving deterministic JSON serialization, atomic output, temporary-root cleanup, bounded profiles, and successful A-D scenarios. Then implement core loop (1/5/10 steps and multi-call), actual/synthetic-I/O tool scheduling, three parallel Subagents, and 10/50/100/500-turn Context scaling. Verify with `python -m pytest tests/test_benchmark_suite.py -q`.

### Task 3: E-I scaling and product scenarios

**TDD cycle:** Extend smoke tests, then implement 10/100/1000 Session catalog/latest/list/lookup/rename-delete/search/recall measurements, scan-vs-FTS/rebuild and disk-size reporting, Memory/Summary incremental/restart measurements, Textual test-pilot product operations, and representative peak-memory measurements. Tests assert correctness/backend selection/bounds, never milliseconds.

### Task 4: J disposable coding tasks and baseline capture

**TDD cycle:** Add fixture cleanup and semantic-verification tests, then implement eight deterministic offline tasks: simple/multi-file Python fixes, add tests, read-only review, create project, optional C++ compile, exploration, and Subagent investigation. Record completion, verification, steps/model/tool/Subagent calls, elapsed time, and protocol status; omit unavailable token usage.

**Run:** `python -m benchmarks.run --profile standard --output benchmark_results/baseline.json`. Preserve this file before any runtime repair.

### Task 5: Failure injection, bounded stress, and minimal repairs

Audit existing coverage before adding tests. Add only missing high-value cases for provider/tool step failure, scheduler/worker failure, SQLite update/rebuild failure, summary persistence failure, plugin partial load/tool failure, child failure/cancellation, TUI worker/event failure, repeated bounded operations, and security/isolation. For each observed defect: RED failing test, minimal GREEN fix, targeted subsystem tests, affected benchmark. Do not fix P3 cosmetics.

### Task 6: Final benchmark, regression, and freeze audit

Run `python -m benchmarks.run --profile standard --output benchmark_results/final.json`, compare baseline/final with actual numbers, then run full pytest, compileall, CLI/doctor/TUI smoke, product/agent/context/session/skill/plugin/subagent E2E groups, forbidden-framework scan, credential scan, path/symlink/isolation regression, and one final global code review. Run a bounded live DeepSeek benchmark only if the existing safe temporary credential mechanism is actually available.

Complete `docs/final-hardening-report.md` with benchmark environment, issues/fixes, exception/lifecycle/security findings, remaining limitations, all 27 acceptance answers, and `CODING AGENT v1 FINAL / NOT FINAL`.

## Plan self-review

- Coverage: benchmark A-J, baseline/final, failure/stress/security/resource/LLM-copy audits, E2E regression, and the final gate are assigned.
- Consistency: benchmarks consume existing public runtime classes; no second Agent Loop or alternative source of truth is introduced.
- Completeness: every interface and deliverable is resolved; optional C++/live cases report `skipped` with a reason.
- Scope: production changes require concrete failing evidence; no frozen subsystem is redesigned pre-emptively.

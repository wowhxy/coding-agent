# Context Management and Memory Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for sequential inline execution. The owner explicitly prohibited subagent/reviewer layers and authorized execution after self-review.

**Goal:** Freeze a mature, persistent, workspace-isolated Context/Memory system with progressive compression, structured relevant memory, temporary session recall, unified budgets, and offline evidence.

**Architecture:** Canonical history remains immutable truth. ContextManager builds a derived view under one ContextPolicy; Summary is session-persistent and incremental, Memory is structured and workspace-persistent, Recall is temporary and backed by a rebuildable SQLite FTS5 index with scan fallback, and Skills remain current-turn subordinate guidance.

**Tech Stack:** Python 3.11+, stdlib JSON/SQLite/FTS5, existing ModelClient/FakeModelClient, pytest; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-context-memory-finalization-design.md`

## Global constraints

- Preserve AgentRunner protocol semantics, ToolRegistry, six tools, Provider abstraction, CLI modes, sessions, and Skills.
- No Agent Framework/SDK, embeddings, vector DB, external RAG/memory provider, Plugin, Multi-Agent, Web UI, or Current Goal redesign.
- All development and automated verification are offline and deterministic; no API key is requested.
- Every Task follows RED -> minimal GREEN -> targeted tests -> one inline review -> necessary fix -> next Task.
- Git writes are prohibited; owner-managed checkpoint suggestions are recorded only in the final report.

---

### Task 1: Progressive Context Compression and ContextPolicy

**Files:** create `src/coding_agent/context_policy.py`, `tests/test_progressive_context.py`; modify `src/coding_agent/context.py`, `src/coding_agent/agent.py`, `tests/test_context.py`, `tests/test_agent.py`.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ContextPolicy:
    max_context_chars: int
    max_tool_output_chars: int
    memory_chars: int = 8_000
    summary_chars: int = 8_000
    recall_chars: int = 6_000
    recent_turns: int = 8
    minimum_recent_turns: int = 2
    summary_trigger_chars: int = 60_000

class ContextManager:
    def needs_summary(self, history, summary=None) -> bool: ...
```

- [x] Write failing tests proving canonical Tool Results remain full while model context receives L1 truncation; L2 prunes only superseded successful read/search/list payloads and keeps protocol pairs/errors/recent tests; L3 emits explicit deterministic activities without semantic inference; impossible anchors raise rather than coarse-slice.
- [x] Run the new tests and confirm failures are due to absent policy/view compression.
- [x] Implement copied-message transformation, stable tool-call identity, conservative pruning, activity rendering in the summary layer, and legacy constructor compatibility.
- [x] Run progressive/context/agent targeted tests, perform one review of protocol integrity and ordering, fix ordinary findings once, then run the same tests again.

### Task 2: Persistent Incremental Summary v1 in Session v4

**Files:** modify `src/coding_agent/summary.py`, `src/coding_agent/session.py`, `src/coding_agent/agent.py`, `src/coding_agent/interactive.py`, `src/coding_agent/scheduler.py`; update `tests/test_summary.py`, `tests/test_persistent_summary.py`, `tests/test_session.py`, relevant interactive/background tests.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class SummaryState:
    text: str
    covered_message_count: int
    updated_at: datetime
    schema_version: int = 1

def SummaryManager.prepare(history, previous=None, *, force=False) -> SummaryState | None: ...
```

- [x] Write failing tests for Session v4 round-trip and v1-v3 migration, invalid schema/coverage/corruption fallback, restart restore, incremental request containing previous summary plus only newly old messages, bounded no-tools output, model failure, and transaction rollback.
- [x] Verify RED, then implement schema migration/validation and summary triggering after deterministic pressure assessment without putting control-plane messages in history.
- [x] Run summary/session/interactive/background targeted tests and Task 1 regression; review coverage arithmetic, atomic persistence, redaction, and failure fallback once; apply necessary fixes and rerun.

### Task 3: Workspace Memory final structured model and candidate quality

**Files:** modify `src/coding_agent/memory.py`, `src/coding_agent/memory_candidate.py`, `src/coding_agent/interactive_shell.py`; update `tests/test_workspace_memory.py`, `tests/test_memory_candidate.py` and candidate shell tests.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    text: str                 # compatibility display
    created_at: datetime
    kind: str
    source: str
    updated_at: datetime
    key: str = ""
    content: str = ""

@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    key: str
    content: str
    kind: str
    source: str
```

- [x] Write failing tests for storage schema v3, v1/v2 migration, explicit/derived keys, exact and normalized dedup, key conflict/update, limits, old API compatibility, candidate structured JSON, real-evidence input, secret/source-dump/hypothesis rejection, confirmation and rejection.
- [x] Verify RED; implement safe key normalization, compatibility display, v3 atomic writes, deterministic match/replace, and one-per-turn no-tools candidate extraction.
- [x] Run memory/candidate/shell targeted tests plus corruption/new-session regressions; review migration losslessness, conflict scope, confirmation authority, and secret safety once; fix and rerun.

### Task 4: Relevant Workspace Memory Retrieval

**Files:** create `src/coding_agent/memory_retrieval.py`, `tests/test_memory_retrieval.py`; modify `src/coding_agent/context.py`, `src/coding_agent/memory.py`, `src/coding_agent/cli.py`, relevant context/memory tests.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ContextMemory:
    id: str
    kind: str
    key: str
    content: str

@dataclass(frozen=True, slots=True)
class MemorySelection:
    included: tuple[ContextMemory, ...]
    dropped_ids: tuple[str, ...]

def select_relevant_memory(items, original_task, latest_user, policy) -> MemorySelection: ...
```

- [x] Write failing tests for small-memory storage order, large-memory query overlap, key/content/kind scoring, constraint priority, stable tie order, Top-12/8,000-character limits, Unicode, empty and corrupt fallback, and strict workspace isolation.
- [x] Verify RED; implement deterministic token scoring and structured ContextManager input while retaining legacy text setter behavior.
- [x] Run retrieval/context/memory/CLI tests and Tasks 1-3 regressions; review determinism, priority, budget accounting, and workspace identity once; fix and rerun.

### Task 5: Session Recall and Rebuildable Search Index

**Files:** create `src/coding_agent/recall.py`, `tests/test_recall.py`; modify `src/coding_agent/session_store.py`, `src/coding_agent/interactive_shell.py`, `src/coding_agent/cli.py`, `src/coding_agent/scheduler.py`; update shell/CLI/background tests.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class RecallEntry:
    session_id: str
    source: str
    excerpt: str
    ordinal: int
    updated_at: datetime
    score: int

class RecallService:
    def search(self, workspace: Path, query: str, *, limit: int = 8) -> tuple[RecallEntry, ...]: ...
    def rebuild(self, workspace: Path) -> None: ...

def references_past(text: str) -> bool: ...
```

- [x] Write failing tests for user/assistant/useful-tool/session-metadata search, bounded ranking, `/recall`, next-turn temporary injection, cue-gated automatic recall, missing/corrupt/stale index rebuild, FTS-unavailable scan fallback, no result, malformed session skip, and different-workspace rejection.
- [x] Verify RED; implement disposable per-workspace FTS5 plus canonical scan, fingerprint refresh, safe corrupt-index replacement, pending explicit recall, and transient automatic recall for foreground/multiline/background.
- [x] Run recall/session/shell/CLI/background targeted tests and prior regressions; review source-of-truth, temporary lifecycle, index recovery, query safety, and isolation once; fix and rerun.

### Task 6: Unified Context Integration and ContextBuildReport

**Files:** modify `src/coding_agent/context.py`, `src/coding_agent/context_policy.py`, `src/coding_agent/agent.py`, `src/coding_agent/cli.py`, `src/coding_agent/interactive_shell.py`, `src/coding_agent/scheduler.py`; create `tests/test_context_report.py`; update Skill/context integration tests.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ContextBuildReport:
    final_context_chars: int
    skills_included: tuple[str, ...]
    memory_ids_included: tuple[str, ...]
    memory_ids_dropped: tuple[str, ...]
    summary_used: bool
    summary_updated: bool
    recall_session_ids: tuple[str, ...]
    stale_results_pruned: int
    tool_results_truncated: int
    activity_turns_compressed: int
    turns_dropped: int

def ContextManager.set_recalled_history(entries: tuple[RecallEntry, ...]) -> None: ...
```

- [x] Write failing tests for exact final order, combined Skill/Memory/Summary/Recall/recent context, pressure order, minimum recent complete turns and protocol batches, safe report fields/counts, transient reset, summary-updated reporting, and no canonical mutation.
- [x] Verify RED; integrate all sources under ContextPolicy, expose `last_report`, and make AgentRunner pass summary-update state without changing its model/tool protocol.
- [x] Run context/report/Skill/Agent/CLI/background tests and all prior task regressions; review unified ordering, safe observability, cleanup, and compatibility once; fix and rerun.

### Task 7: Full Offline E2E, documentation, regression, and freeze verification

**Files:** create `tests/test_context_memory_final_e2e.py`; update `README.txt`, `tests/test_readme.py`; modify only defects revealed by E2E/final review.

- [x] Build one deterministic multi-workspace E2E using FakeModelClient, temporary C++/Python workspaces, real persistence, ContextManager, Memory, Sessions, Recall, Skills, and ToolRegistry. Cover Long Session L1-L4, restart/incremental Summary, `/new` shared Memory without History/Summary inheritance, explicit/automatic Recall, workspace isolation, and Skill+Memory+Summary+Recall+Tool Calling.
- [x] Run the E2E and targeted context/summary/memory/recall/Skill suites; update the <=1,000-character submission README with final behavior and limitations.
- [x] Perform exactly one final inline review against the fourteen freeze questions and all failure cases; implement one necessary fix wave without another reviewer.
- [x] Run full pytest, compileall, CLI smoke, original Agent Loop/six tools/Provider/demo regressions, forbidden framework/dependency scan, credential scan, and placeholder scan. Report exact outputs and whether only optional Live DeepSeek quality verification remains.

## Plan self-review

- **Requirement coverage:** Tasks 1-7 map to every Design section and all owner-required E2E/failure/regression/freeze checks.
- **Architecture consistency:** History is never compressed in place; Summary coverage is tail-relative; Memory carries structured keys; Recall remains derived and temporary; ContextManager alone assembles the view.
- **Type consistency:** ContextPolicy precedes all consumers; MemorySelection feeds ContextManager; RecallEntry feeds transient recalled history; ContextBuildReport contains identifiers/counts only.
- **Backward compatibility:** legacy ContextManager arguments, Memory `.text`/manual add, Session v1-v3, Memory v1-v2, existing CLI constructors, and no-Skill/no-Recall flows remain supported.
- **Persistence safety:** Session JSON and Memory JSON are authoritative and atomic; Recall SQLite is disposable; optional corrupt states fall back without overwriting canonical data.
- **Placeholder scan:** every interface, limit, lifecycle, migration, test command class, and fallback has a concrete ruling; no unfinished marker remains.
- **Scope check:** exactly seven tasks; no prohibited framework, SDK, retrieval service, goal redesign, UI, provider, tool, or autonomous permanent-memory expansion.

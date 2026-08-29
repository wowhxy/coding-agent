# Subagent System v1 FINAL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Execute the six Tasks in order with TDD, one concise review per Task, and no Git writes.

**Goal:** Add true parallel, read-only Subagents that reuse AgentRunner while the parent remains the only workspace writer.

**Architecture:** A synchronous `delegate_tasks` control tool calls a bounded ThreadPool-based `SubagentManager`. Each child owns a model client, read-only ToolRegistry, ContextManager, history, and existing AgentRunner; the parent receives only ordered bounded results.

**Tech Stack:** Python 3.11 stdlib (`concurrent.futures`, `threading`, `json`, dataclasses), existing protocol/AgentRunner/ContextManager/ToolRegistry, pytest and FakeModelClient; no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-subagent-system-final-design.md`

## Global constraints

- Single Python process and at most three concurrent child threads.
- Child tools are exactly `list_files`, `search_text`, and `read_file`.
- Parent is the only writer and command executor; dispatch is synchronous until join.
- Every child owns mutable runtime state and a separately created ModelClient.
- Child histories/results are ephemeral; only bounded structured results enter parent history.
- No Plugin tools, recursive delegation, child Memory writes, child sessions, worktrees, multiprocessing, or Agent framework.
- Current repository Git writes remain owner-managed and prohibited.

---

### Task 1: Models, profiles, and read-only ToolPolicy

**Files:**
- Create: `src/coding_agent/subagents/__init__.py`
- Create: `src/coding_agent/subagents/models.py`
- Create: `src/coding_agent/subagents/profiles.py`
- Create: `src/coding_agent/subagents/policy.py`
- Create: `tests/test_subagent_models.py`
- Create: `tests/test_subagent_policy.py`

**Interfaces:**

```python
class SubagentRole(str, Enum): EXPLORE; ANALYSIS; REVIEW
class SubagentContextMode(str, Enum): FRESH; FORK
@dataclass(frozen=True, slots=True)
class SubagentRequest: task: str; role: SubagentRole; context_mode: SubagentContextMode
@dataclass(frozen=True, slots=True)
class SubagentTask: id: str; task: str; role: SubagentRole; context_mode: SubagentContextMode
@dataclass(frozen=True, slots=True)
class SubagentResult: task_id: str; role: SubagentRole; status: RunStatus; result: str; steps: int; error: str | None
@dataclass(frozen=True, slots=True)
class SubagentLimits: ...
def build_read_only_registry(workspace: Path) -> ToolRegistry: ...
def subagent_system_prompt(role: SubagentRole) -> str: ...
```

- [ ] Write RED validation/profile tests for all roles/modes, immutable models, positive internally consistent limits, and bounded task/id strings.
- [ ] Write RED ToolPolicy tests asserting exact ordered definitions `list_files`, `search_text`, `read_file`; dispatch write/replace/command/delegate/plugin names as UNKNOWN_TOOL; verify workspace containment and symlink behavior through existing tools.
- [ ] Implement the minimal models, three fixed subordinate profiles, and registry builder reusing existing WorkspacePaths/file tool factories.
- [ ] Run Task 1 tests plus existing file/registry tests, then one review of scope, provider-safe schemas, and read-only enforcement; fix important findings once and rerun.

### Task 2: Independent child composition and fresh/fork context

**Files:**
- Create: `src/coding_agent/subagents/manager.py`
- Modify: `src/coding_agent/agent.py`
- Create: `tests/test_subagent_context.py`
- Create: `tests/test_subagent_child.py`

**Interfaces:**

```python
ModelClientFactory = Callable[[], ModelClient]
ContextManagerFactory = Callable[[], ContextManager]
class SubagentManager:
    def begin_parent_run(self) -> None: ...
    def observe_parent_context(self, messages: tuple[Message, ...]) -> None: ...
    def set_workspace_memories(self, items: tuple[ContextMemory, ...]) -> None: ...
    def set_active_skills(self, skills: tuple[ActiveSkill, ...]) -> None: ...
    def run_child(self, task: SubagentTask) -> SubagentResult: ...
```

- [ ] Write RED tests proving each child gets a distinct client/history/context/registry, client close occurs on every status, child max steps is enforced, and configured sensitive values are redacted.
- [ ] Write RED fresh tests proving only delegated task, relevant memory, and active Skills are present; write fork tests proving bounded parent snapshot presence and no parent/child history mutation or object sharing.
- [ ] Add generic optional `run_start_hook` and `context_snapshot_sink` AgentRunner callbacks without changing loop/termination behavior; implement one-child composition with no summary, recall, persistence, Plugin, or delegation capability.
- [ ] Run Task 2 plus Agent/Context/Memory/Skill regressions, then one review of isolation, snapshot trust, lifecycle, and frozen architecture boundaries; fix once and rerun.

### Task 3: True parallel manager, limits, ordering, and failure isolation

**Files:**
- Modify: `src/coding_agent/subagents/manager.py`
- Modify: `src/coding_agent/subagents/models.py`
- Create: `tests/test_subagent_manager.py`
- Create: `tests/test_subagent_parallelism.py`

**Interfaces:**

```python
class SubagentManager:
    def delegate(self, requests: tuple[SubagentRequest, ...]) -> tuple[SubagentResult, ...]: ...
class SubagentLimitError(ValueError): code: str
```

- [ ] Write RED Barrier/Event tests proving at least two workers overlap and each gets an independent fake client; do not assert elapsed time.
- [ ] Write RED tests for input-order aggregation despite reverse completion, FINAL/MODEL_ERROR/MAX_STEPS isolation, worker exception conversion, no sibling cancellation, max three per batch, max six per parent run, run reset, exact duplicate rejection, and depth one.
- [ ] Write RED result-budget tests for 6000 per child and 16000 total, deterministic truncation, bounded errors, stable task IDs, and sensitive-value redaction.
- [ ] Implement one bounded ThreadPool per batch, main-thread event aggregation, fingerprint/run accounting under a lock, ordered results, and safe per-child exception conversion.
- [ ] Run Tasks 1–3 and full Agent protocol tests, then one review of actual concurrency, races, determinism, exhaustion semantics, and exception isolation; fix once and rerun.

### Task 4: `delegate_tasks` control tool and parent Agent integration

**Files:**
- Create: `src/coding_agent/subagents/control.py`
- Modify: `src/coding_agent/tools/registry.py`
- Modify: `src/coding_agent/system_prompt.py`
- Create: `tests/test_delegate_tasks_tool.py`
- Create: `tests/test_subagent_parent_integration.py`

**Interfaces:**

```python
def create_delegate_tasks_tool(manager: SubagentManager) -> RegisteredTool: ...
# source: control:subagent
```

- [ ] Write RED schema/local validation tests for one-to-three task objects, exact fields, role/mode defaults, bounded strings, non-object arguments, and stable JSON ToolResult encoding.
- [ ] Write RED parent-loop tests: model calls delegate, receives only bounded ordered results, continues to parent write/command/final, and parent canonical history contains no child ToolCalls/ToolResults.
- [ ] Write RED control failures for `SUBAGENT_LIMIT_REACHED`, `SUBAGENT_DUPLICATE`, and unrecoverable `SUBAGENT_INTERNAL_ERROR`; individual child failures must keep control ToolResult successful.
- [ ] Register the source-owned control tool, connect AgentRunner callbacks, and add concise parent delegation guidance while preserving all existing tool dispatch behavior.
- [ ] Run control/parent/Agent/registry tests, then one review of protocol relations, semantic termination, recursive denial, and parent single-writer behavior; fix once and rerun.

### Task 5: CLI, session, background, Memory, Skill, and Plugin composition

**Files:**
- Modify: `src/coding_agent/cli.py`
- Modify: `src/coding_agent/interactive_shell.py`
- Create: `tests/test_subagent_cli.py`
- Create: `tests/test_subagent_runtime_integration.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class SubagentEvent: kind: str; task_id: str | None; role: SubagentRole | None; status: RunStatus | None; message: str
```

- [ ] Write RED one-shot and interactive tests proving delegate is visible to the parent, current memories/Skills reach children read-only, enabled Plugin tools remain parent-only, and child tasks never appear in session listings/indexes.
- [ ] Write RED background tests proving each background runtime has an isolated manager/client factory/read-only child registry and preserves submit-time Plugin behavior only for its parent.
- [ ] Write RED CLI event tests for batch start, stable running lines, completion status, and collection without child tool traces, result bodies, secrets, or tracebacks.
- [ ] Compose a manager/control tool inside the existing one-shot, interactive, and background runtime factories; refresh immutable memory/Skill inputs before delegation, close child clients, and emit redacted concise events without changing Scheduler, Session, or Plugin persistence APIs/schemas.
- [ ] Run CLI/interactive/background/Session/Skill/Memory/Plugin tests, then one review of lifecycle, state pollution, output safety, and existing subsystem freeze; fix once and rerun.

### Task 6: Offline parser E2E, documentation, and final verification

**Files:**
- Create: `tests/test_subagent_system_e2e.py`
- Create: `docs/subagent-demo.md`
- Modify: `README.txt`
- Modify: `tests/test_readme.py`
- Modify: `docs/superpowers/plans/2026-08-29-subagent-system-final-plan.md`

- [ ] Write the deterministic parser fixture E2E using real AgentRunner/ToolRegistry/ContextManager/SubagentManager and independent FakeModelClients: three children inspect implementation/tests/call-sites in parallel, parent receives bounded results, edits parser, runs tests, and finishes.
- [ ] Add E2E assertions for child write/replace/command/delegate denial, parent-only mutation, canonical parent isolation, fresh/fork inputs, mixed child failures, every limit, and Barrier/Event execution overlap.
- [ ] Add a repeatable two-minute PowerShell demo guide showing three parallel read-only investigations followed by parent edit/test/final; keep README within 1000 characters and state single-process/single-writer/no-worktree limits.
- [ ] Run Subagent E2E and all targeted suites; perform the Task's one review of demo credibility, all sixteen freeze questions, thread safety, requirements, and scope; apply one necessary fix wave.
- [ ] Perform exactly one final inline review, then run all requested targeted tests, full pytest, compileall, CLI smoke, existing subsystem regressions, forbidden-framework scan, credential scan, and placeholder scan. Record exact evidence and stop for optional Live DeepSeek Subagent E2E.

## Owner-managed Git checkpoints

Record suggestions after Tasks 2, 4, and 6, but never execute Git writes or pause implementation for commits. Recommended logical messages are `feat: add isolated read-only subagent runtimes`, `feat: add parallel delegation control tool`, and `feat: integrate and verify subagent orchestration`.

## Execution status

All six Tasks were implemented in order with failing tests, minimal changes,
targeted regression, and one review per Task. Final verification evidence is
reported in the completion handoff; Git writes remain owner-managed.

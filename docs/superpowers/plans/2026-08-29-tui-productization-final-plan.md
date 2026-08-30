# Coding Agent TUI Productization Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. The user explicitly excluded Subagent-Driven Development for this wave. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a mature, responsive, testable Textual product over the existing coding-agent core while preserving every one-shot and classic interactive behavior.

**Architecture:** A synchronous `CodingAgentService` composes and delegates to existing core managers, projects immutable product state, and publishes redacted typed events. A Textual application runs foreground agent work in a thread worker and consumes those events on the UI thread; core protocol, persistence, and tool semantics remain authoritative.

**Tech Stack:** Python 3.11+, Textual 8.x, stdlib threading/difflib/pathlib, pytest, existing FakeModelClient and coding-agent core.

**Spec:** `docs/superpowers/specs/2026-08-29-tui-productization-final-design.md`

## Global Constraints

- Keep `AgentRunner`, provider abstraction, Tool Calling, six built-in coding tools, `ToolRegistry`, Context/Memory, Skills, Plugins, Subagents, and termination semantics compatible.
- TUI reads typed application state/events, never CLI stdout and never private reasoning.
- All normal tests are offline, deterministic, and require no real credential.
- Secrets are never persisted, rendered, logged, diffed, or included in events/reports.
- Keep one-shot CLI and task-omitted classic interactive mode; add explicit `coding-agent tui` and `coding-agent doctor` routes.
- Use cooperative cancellation only; do not kill threads or rewrite the core as async.
- No Git writes. Owner-managed checkpoint suggestions are recorded only in the final report.
- No Web UI, desktop UI, Agent Framework/SDK, new Context/Memory architecture, writable Subagents, worktrees, or automatic Git operations.

## File map

- `src/coding_agent/application/events.py`: redacted immutable product-event contract and adapters.
- `src/coding_agent/application/state.py`: immutable status, session, conversation, activity, change, verification, and candidate views.
- `src/coding_agent/application/commands.py`: deterministic slash-command grammar and discoverable help records.
- `src/coding_agent/application/changes.py`: bounded workspace snapshots, diffs, and verification evidence derived from canonical tool protocol.
- `src/coding_agent/application/diagnostics.py`: credential-safe doctor checks and text rendering.
- `src/coding_agent/application/service.py`: reusable facade, core composition, task transaction, management methods, cleanup.
- `src/coding_agent/tui/app.py`: Textual app, worker boundary, event handling, key actions.
- `src/coding_agent/tui/widgets.py`: session, conversation/activity, composer, and status widgets.
- `src/coding_agent/tui/screens.py`: help, confirmation, management, diff, and candidate dialogs.
- `src/coding_agent/tui/theme.tcss`: full and compact layouts.
- `src/coding_agent/cli.py`: explicit `tui`/`doctor` dispatch while preserving old parsing.
- `pyproject.toml`: Textual runtime dependency.
- `README.txt`, `docs/tui-guide.md`, `docs/tui-demo.md`: concise user, operational, and two-minute demo documentation.
- `tests/test_application_*.py`, `tests/test_tui_*.py`: deterministic component and product tests.

---

### Task 1: Product contracts, commands, and safe projections

**Files:**
- Create: `src/coding_agent/application/__init__.py`
- Create: `src/coding_agent/application/events.py`
- Create: `src/coding_agent/application/state.py`
- Create: `src/coding_agent/application/commands.py`
- Test: `tests/test_application_events.py`
- Test: `tests/test_application_commands.py`
- Test: `tests/test_application_state.py`

**Interfaces:**

```python
class ProductEventKind(str, Enum):
    TASK_STARTED = "task_started"
    MODEL_WAITING = "model_waiting"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    SUBAGENT_BATCH = "subagent_batch"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_FINISHED = "subagent_finished"
    FINAL_RESPONSE = "final_response"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    SESSION_CHANGED = "session_changed"
    STATE_CHANGED = "state_changed"
    FILE_CHANGES = "file_changes"
    VERIFICATION = "verification"
    MEMORY_CANDIDATE = "memory_candidate"
    RECALL_RESULT = "recall_result"
    ERROR = "error"
    NOTICE = "notice"

class ActivityStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class ProductEvent:
    kind: ProductEventKind
    timestamp: datetime
    session_id: str | None
    task_id: str | None
    step: int | None
    title: str
    detail: str = ""
    status: ActivityStatus | None = None
    metadata: tuple[tuple[str, str], ...] = ()

def adapt_agent_event(event: AgentEvent, *, session_id: str, task_id: str,
                      sensitive_values: tuple[str, ...]) -> ProductEvent:
    raise NotImplementedError
def adapt_subagent_event(event: SubagentEvent, *, session_id: str, task_id: str,
                         sensitive_values: tuple[str, ...]) -> ProductEvent:
    raise NotImplementedError
def parse_command(text: str) -> ProductCommand | None:
    raise NotImplementedError
def command_help() -> tuple[CommandHelp, ...]:
    raise NotImplementedError

class AgentState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    ERROR = "error"
    CLOSED = "closed"

@dataclass(frozen=True, slots=True)
class ProductStatus:
    provider: str
    model: str
    workspace: Path
    session_id: str
    agent_state: AgentState
    context_chars: int
    context_limit: int
    summary_active: bool
    memory_count: int
    active_skills: tuple[str, ...]
    enabled_plugins: tuple[str, ...]
    active_subagents: int

@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    status: ProductStatus
    sessions: tuple[SessionView, ...]
    conversation: tuple[ConversationItem, ...]
    changes: tuple[ChangeView, ...]
    verifications: tuple[VerificationView, ...]
```

- [ ] Write tests proving event mapping is stable, sink-facing data is immutable, credentials are redacted, invalid metadata is rejected, commands cover every required slash form, arguments are trimmed but not silently rewritten, unknown/empty commands produce concise typed errors, and state views reject invalid status combinations.
- [ ] Run `python -m pytest -q tests/test_application_events.py tests/test_application_commands.py tests/test_application_state.py` and confirm collection/import failures before implementation.
- [ ] Implement the enums/dataclasses/adapters/parser and exported API with no core mutation.
- [ ] Rerun the targeted command and then `python -m pytest -q`.
- [ ] Perform one concise task review for immutability, redaction, grammar coverage, and unused fields; make any necessary local fix and rerun the targeted tests.

### Task 2: File changes, diffs, and verification evidence

**Files:**
- Create: `src/coding_agent/application/changes.py`
- Test: `tests/test_application_changes.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    text: str | None
    digest: str

@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: Path
    files: tuple[FileSnapshot, ...]

def snapshot_workspace(workspace: Path, *, max_files: int = 2000,
                       max_file_chars: int = 200_000) -> WorkspaceSnapshot:
    raise NotImplementedError
def compare_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot,
                      *, max_diff_chars: int = 40_000) -> tuple[ChangeView, ...]:
    raise NotImplementedError
def verification_views(messages: tuple[Message, ...]) -> tuple[VerificationView, ...]:
    raise NotImplementedError
def activity_views(messages: tuple[Message, ...], start: int = 0) -> tuple[ActivityView, ...]:
    raise NotImplementedError
```

- [ ] Write tests for add/modify/delete diffs, line counts, deterministic ordering, binary/large-file bounds, symlink skipping, workspace containment, cache/`.git` exclusion, malformed tool JSON, successful and failed `execute_command` evidence, and the distinction between final claims and actual verification.
- [ ] Run `python -m pytest -q tests/test_application_changes.py` and confirm failure because the module is absent.
- [ ] Implement bounded snapshots and canonical-protocol projections using stdlib only; never invoke Git or parse rendered CLI text.
- [ ] Run the targeted test and `python -m pytest -q`.
- [ ] Review scan cost, secret redaction boundary, symlink behavior, diff caps, and false verification claims once; fix material issues and rerun targeted tests.

### Task 3: CodingAgentService and typed task lifecycle

**Files:**
- Create: `src/coding_agent/application/service.py`
- Modify: `src/coding_agent/application/__init__.py`
- Test: `tests/test_application_service.py`
- Test: `tests/test_application_service_management.py`
- Test: `tests/test_application_service_e2e.py`

**Interfaces:**

```python
EventSubscriber = Callable[[ProductEvent], None]

class CodingAgentService:
    @classmethod
    def create(cls, config: RuntimeConfig, provider_name: str, session_home: Path,
               client_factory: ClientFactory, *, new_session: bool = False,
               resume_session: str | None = None) -> "CodingAgentService":
        raise NotImplementedError
    def subscribe(self, sink: EventSubscriber) -> Callable[[], None]:
        raise NotImplementedError
    def snapshot(self) -> ProductSnapshot:
        raise NotImplementedError
    def submit_task(self, text: str) -> RunResult:
        raise NotImplementedError
    def cancel_task(self) -> bool:
        raise NotImplementedError
    def close(self) -> None:
        raise NotImplementedError
    # session, Memory, Skill, Plugin, Recall, and candidate-confirmation methods
```

- [ ] Write service tests with injected FakeModel clients and temporary real stores for startup/resume, streaming events, Tool/Subagent events, non-overlapping foreground tasks, cooperative cancellation without commit, transactional success persistence, model/tool errors, context status, exact-once cleanup, event-sink isolation, Session CRUD/isolation, Memory CRUD/conflicts, Skill activation, Plugin trust/enable-disable, Recall, and confirmed/rejected candidates.
- [ ] Run the three targeted files and confirm the missing facade fails.
- [ ] Implement service composition by reusing `InteractiveSession`, `JsonSessionStore`, `WorkspaceMemoryStore`, `SkillActivator`, `PluginManager`, `RecallService`, `SubagentManager`, and the existing runner/registry factories. Emit events around public operations and adapt existing core callbacks. Use a lock only for service lifecycle/running state; do not hold it while the model or tools run.
- [ ] Run the targeted tests and `python -m pytest -q`.
- [ ] Review ownership/cleanup, transactional cancellation, core duplication, workspace isolation, and event redaction once; fix necessary issues and rerun the three targeted files.

### Task 4: Doctor diagnostics and compatible CLI routing

**Files:**
- Create: `src/coding_agent/application/diagnostics.py`
- Modify: `src/coding_agent/cli.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_doctor.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str

def run_doctor(*, workspace: Path, provider: str, model: str | None,
               api_key_env: str, environ: Mapping[str, str],
               session_home: Path) -> tuple[DiagnosticCheck, ...]:
    raise NotImplementedError
def render_doctor(checks: tuple[DiagnosticCheck, ...]) -> str:
    raise NotImplementedError
```

- [ ] Write tests proving `doctor` checks Python/workspace/provider/credential-presence/Git/storage/Skills/Plugins, deletes its probe, never constructs a model client, never prints credential values, reports partial failures, and returns nonzero only when product readiness is blocked. Add regression tests for legacy one-shot and classic interactive parsing plus the `tui` dispatch seam with an injected launcher.
- [ ] Run `python -m pytest -q tests/test_doctor.py tests/test_cli.py` and verify the new cases fail.
- [ ] Implement early exact subcommand routing for `doctor` and `tui`, reusing existing provider/config arguments without changing ordinary task parsing. Keep the TUI launcher lazy-imported.
- [ ] Run the targeted files and `python -m pytest -q`.
- [ ] Review credential output, temp-file cleanup, help text, exit codes, and parser compatibility once; fix and rerun targeted tests.

### Task 5: Textual shell, input, sessions, and responsive layout

**Files:**
- Modify: `pyproject.toml`
- Create: `src/coding_agent/tui/__init__.py`
- Create: `src/coding_agent/tui/app.py`
- Create: `src/coding_agent/tui/widgets.py`
- Create: `src/coding_agent/tui/screens.py`
- Create: `src/coding_agent/tui/theme.tcss`
- Create: `tests/test_tui_app.py`
- Create: `tests/test_tui_input.py`
- Create: `tests/test_tui_sessions.py`
- Create: `tests/test_tui_responsive.py`

**Interfaces:**

```python
class CodingAgentApp(App[None]):
    def __init__(self, service: CodingAgentService) -> None:
        raise NotImplementedError
    def action_submit(self) -> None:
        raise NotImplementedError
    def action_cancel_or_clear(self) -> None:
        raise NotImplementedError
    def apply_product_event(self, event: ProductEvent) -> None:
        raise NotImplementedError

def run_tui(service: CodingAgentService) -> int:
    raise NotImplementedError
```

- [ ] Add `textual>=8,<9`, install the editable test environment, and write Pilot tests for first-run content, `Ctrl+Enter` submit, Enter newline, empty protection, command history, running-state disablement, cooperative cancel, Esc/input focus, sidebar toggle, new/switch/rename/delete confirmation, quit cleanup, event-thread handoff, and visible composer/conversation at 80x24 and a larger size.
- [ ] Run the four targeted files and verify failure before UI implementation.
- [ ] Implement the Textual app and widgets. Run `submit_task` in `@work(thread=True, exclusive=True)`; publish thread-safe Textual messages and mutate widgets only on the UI thread. Mount conversation items incrementally and keep the editor visible.
- [ ] Run the targeted UI tests, then `python -m pytest -q`.
- [ ] Review keyboard conflicts, discoverability, small-terminal layout, worker shutdown, cancellation, and empty state once; fix high-impact issues and rerun targeted UI tests.

### Task 6: Activity, management, diff, verification, and error UX

**Files:**
- Modify: `src/coding_agent/tui/app.py`
- Modify: `src/coding_agent/tui/widgets.py`
- Modify: `src/coding_agent/tui/screens.py`
- Modify: `src/coding_agent/tui/theme.tcss`
- Create: `tests/test_tui_activity.py`
- Create: `tests/test_tui_management.py`
- Create: `tests/test_tui_errors.py`

- [ ] Write Pilot tests showing compact/expanded Tool activities with bounded detail, live Subagent tree states, streaming text, changed-file/diff summaries, verification success/failure distinct from final claims, context/status counts, Memory CRUD and confirmation, Skill use/off/clear, Plugin enable/disable with trust warning, Recall, actionable categorized errors, and no sensitive rendering.
- [ ] Run the three targeted files and verify the missing UX behavior fails.
- [ ] Implement activity cards and management/detail screens by consuming `ProductEvent` and `ProductSnapshot` only. Add `/help` command/binding discoverability and modal confirmations for destructive product actions.
- [ ] Run the targeted files and `python -m pytest -q`.
- [ ] Review visual noise, evidence labeling, private-reasoning exclusion, trust copy, error recovery, and expandable-output bounds once; fix high-impact issues and rerun targeted tests.

### Task 7: Offline product E2E and focused product iterations

**Files:**
- Create: `tests/test_tui_product_e2e.py`
- Create: `tests/fixtures/tui_demo/buggy_project/parser.py`
- Create: `tests/fixtures/tui_demo/buggy_project/test_parser.py`
- Modify: TUI/application files only when a measured UX defect is fixed

- [ ] Write a deterministic Pilot E2E using a temporary copied fixture, real persistence, real `CodingAgentService`, real core/registry/tools, and scripted FakeModel turns: launch, submit, Tool and three-Subagent visibility, edit, test evidence, final Markdown, changed files, persistence, quit, restart, and automatic resume.
- [ ] Add product scenarios for cancellation/recovery, provider failure/retry readiness, session switch with Memory retained but history isolated, and responsive 80x24 state.
- [ ] Run `python -m pytest -q tests/test_tui_product_e2e.py` and fix only evidence-backed failures.
- [ ] Product iteration 1: run all TUI/application tests plus the scripted E2E; review first-use clarity, active work, changed files, actual verification, sessions, management discovery, errors, responsiveness, and noise; apply only high-impact fixes and rerun.
- [ ] Product iteration 2 only if iteration 1 leaves an acceptance-impacting issue; repeat the same measured test/review/fix cycle. Use iteration 3 only for a remaining acceptance blocker.
- [ ] Run `python -m pytest -q` after the final product iteration and perform one concise task review of end-to-end truthfulness, determinism, and restart safety.

### Task 8: Documentation, final offline verification, bounded Live smoke, and freeze review

**Files:**
- Modify: `README.txt`
- Create: `docs/tui-guide.md`
- Create: `docs/tui-demo.md`
- Create or modify: a verification-only live harness under `scripts/` only if the existing safe live mechanism cannot drive `coding-agent tui`
- Test: `tests/test_readme.py`
- Test: `tests/test_packaging.py`

- [ ] Update concise README content within the project PDF's README limit: install, provider setup, `coding-agent tui`, keys, Sessions, Memory, Skills, Plugins, Subagents, doctor, security, one-shot compatibility. Put detailed operation and the deterministic two-minute demo in docs.
- [ ] Run targeted application/TUI/README/packaging tests, `python -m pytest -q`, `python -m compileall -q src tests`, CLI help/doctor/one-shot/classic-interactive smokes, 80x24 Pilot smoke, and the complete offline product E2E; record exact outputs.
- [ ] Scan source/docs/tests for forbidden Agent frameworks/SDKs, accidental credential patterns, raw authorization output, Git mutation features, placeholders, and scope violations.
- [ ] If the approved live credential mechanism is available, run one bounded disposable-workspace DeepSeek TUI coding smoke and one short parallel investigation only when the first smoke is stable. For any failure, classify, add an offline regression, make the smallest fix, rerun targeted/full regression, and retry that live case no more than twice.
- [ ] Perform one final product review against all 24 acceptance items, then rerun full regression after the final fix. Declare `TUI PRODUCT v1 FINAL` and architecture frozen only if every mandatory item, including Live, has real passing evidence; otherwise report `NOT FINAL` with the exact external or technical blocker.
- [ ] Produce one final report with architecture/framework/UX, iteration history, exact automated/offline/live evidence, regression/performance/security/limitations/demo readiness, owner-managed Git checkpoint file groups/messages, and the freeze judgment.

## Plan self-review

- **Spec coverage:** Tasks 1-3 cover the facade/events/state and frozen-core boundary; Tasks 4-6 cover diagnostics, CLI, full UI, commands, management, activity, changes, verification, errors, cancellation, and responsiveness; Tasks 7-8 cover deterministic product E2E, measured iterations, docs, Live, regression, security, demo, and freeze.
- **Architecture/interface consistency:** `CodingAgentService` is the sole TUI business boundary; `ProductEvent` is the live boundary; `ProductSnapshot` is the pull-state boundary; Textual never consumes CLI output or core private state.
- **Backward compatibility:** Existing positional one-shot and no-task classic interactive routes remain unchanged; new product commands are exact first-position routes.
- **Persistence safety:** Existing transactional `InteractiveSession` and stores remain authoritative; cancellation and UI failure do not commit incomplete turns.
- **Scope:** Textual plus presentation/application code only; no new Agent, Context, Memory, provider, or Git-write subsystem.
- **Placeholder scan:** No deferred implementation markers or undefined follow-on phases remain. Conditional second/third UX iterations and Live execution have explicit gates from the approved requirements.

# Plugin System v1 FINAL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` inline. Execute Tasks 1–6 in order with TDD, one concise review per Task, and no Git writes.

**Goal:** Add a trusted local Python Plugin System that dynamically extends the existing ToolRegistry and includes a constrained `git-readonly` demonstration plugin without changing AgentRunner or frozen Context/Memory/Skill architecture.

**Architecture:** A manifest-only discovery and persistence manager loads executable plugins only from `<CODING_AGENT_HOME>/plugins` after explicit enable. Plugin tools use existing `RegisteredTool`/`ToolResult` contracts and enter ToolRegistry through transactional source-owned registration. Foreground, one-shot, and isolated background registries restore or snapshot enabled names independently.

**Tech Stack:** Python 3.11 stdlib (`json`, `pathlib`, `importlib`, `subprocess`, atomic `os.replace`), existing coding-agent protocol, pytest/FakeModelClient; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-plugin-system-final-design.md`

## Global constraints

- Executable plugins load only from `<CODING_AGENT_HOME>/plugins`; never from a workspace.
- Discovery never imports `plugin.py`; import occurs only during explicit enable or restore of a previously enabled name.
- Plugin failures cannot alter built-ins or leave partial tool registration.
- AgentRunner, Context/Memory, and Skill architecture remain unchanged.
- Plugin state uses one atomic local JSON file; no database or remote behavior.
- All tests are offline and deterministic; temporary Git repositories are the only locations where test setup may perform Git writes.
- Git writes to the current `D:\proj` repository remain prohibited and owner-managed.

---

### Task 1: Plugin models, manifest discovery, and path/trust rules

**Files:**
- Create: `src/coding_agent/plugins.py`
- Create: `tests/test_plugins.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str
    description: str
    entrypoint: str
    package_dir: Path

@dataclass(frozen=True, slots=True)
class PluginInfo:
    metadata: PluginMetadata
    status: str

@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    code: str
    plugin_name: str | None
    message: str

@dataclass(frozen=True, slots=True)
class PluginContext:
    workspace: Path

class PluginManager:
    def discover(self) -> tuple[PluginInfo, ...]: ...
```

- [x] Write tests for valid strict manifest parsing, missing/extra fields, invalid JSON/types/name/version/description, duplicate manifest names in differently named package directories, no workspace discovery, absolute/`..`/outside/missing entrypoints, and package/manifest/entrypoint symlink rejection. A sentinel `plugin.py` side effect must remain absent after discovery.
- [x] Run `python -m pytest -q tests/test_plugins.py --basetemp .pytest_cache/basetemp-plugin-task1-red` and confirm failures are missing Plugin APIs.
- [x] Implement immutable models, canonical home/plugin root resolution, strict bounded validation, symlink checks with `lstat`, duplicate invalidation, stable ordering, and sanitized diagnostics. Discovery must read only `plugin.json`.
- [x] Run `tests/test_plugins.py`, then the full suite; perform one review of trust boundary, containment, lazy import, diagnostics, and unnecessary scope; fix important findings once and rerun.

### Task 2: Transactional ToolRegistry ownership and plugin loading

**Files:**
- Modify: `src/coding_agent/tools/registry.py`
- Modify: `src/coding_agent/plugins.py`
- Modify: `tests/test_tool_registry.py`
- Create: `tests/test_plugin_loading.py`

**Interfaces:**

```python
class ToolRegistry:
    def register_many(
        self, tools: tuple[RegisteredTool, ...], *, source: str
    ) -> None: ...
    def unregister_source(self, source: str) -> tuple[str, ...]: ...
    def source_of(self, tool_name: str) -> str | None: ...

class PluginManager:
    def enable(self, name: str, *, persist: bool = True) -> PluginInfo: ...
    def disable(self, name: str, *, persist: bool = True) -> PluginInfo | None: ...
```

- [x] Write ToolRegistry RED tests for source ownership, valid batch insertion order, whole-batch rejection on duplicate/collision/invalid type/name/schema/handler, built-in removal refusal, and plugin-only cleanup.
- [x] Write loader RED tests for lazy import, `get_tools(PluginContext)` invocation, missing function, import exception, invalid return/tool, built-in collision, plugin-plugin collision, no partial registration, module cleanup, and built-ins surviving every failure. Diagnostics must not contain raised exception text or synthetic secrets.
- [x] Run the Task 2 test files and confirm expected API/behavior failures.
- [x] Implement provider-safe tool validation, atomic source registration, unique hashed module names, safe import, complete contract validation before registry mutation, and source-specific unload. Do not modify AgentRunner.
- [x] Run Task 1–2 tests plus Agent/registry tests and full suite; perform one review of atomicity, ownership, collision handling, import cleanup, and protocol reuse; fix once and rerun.

### Task 3: Enabled-state persistence and interactive CLI

**Files:**
- Modify: `src/coding_agent/plugins.py`
- Modify: `src/coding_agent/interactive_shell.py`
- Modify: `src/coding_agent/cli.py`
- Create: `tests/test_plugin_commands.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

```python
class PluginManager:
    @property
    def enabled_names(self) -> tuple[str, ...]: ...
    def restore_enabled(self) -> tuple[PluginInfo, ...]: ...
    # state: {"schema_version": 1, "enabled": [sorted names]}

class InteractiveShell:
    # optional constructor dependency: plugin_manager: PluginManager | None
```

- [x] Write persistence RED tests for atomic sorted state, duplicate enable/disable idempotency, restart restore, corrupt/unsupported state fallback, missing previously enabled plugin diagnostic, import/manifest failure recovery, and explicit disable of a missing persisted name.
- [x] Write shell/CLI RED tests for `/plugins`, `/plugin enable <name>`, `/plugin disable <name>`, exact table columns, clean errors, tool definitions changing only after successful activation, startup restore in interactive and one-shot modes, and unknown-command help mentioning plugins.
- [x] Run Task 3 tests and confirm missing persistence/commands cause the failures.
- [x] Implement strict state parsing and atomic replacement, idempotent state transitions, table rendering, sanitized warnings, and default-registry-plus-restored-plugins construction. Plugin failure must never make CLI startup fail.
- [x] Run plugin/CLI/interactive tests and full suite; perform one review of explicit trust, persistence authority, CLI usability, error redaction, and one-shot parity; fix once and rerun.

### Task 4: Background snapshot and runtime isolation

**Files:**
- Modify: `src/coding_agent/scheduler.py`
- Modify: `src/coding_agent/interactive_shell.py`
- Modify: `src/coding_agent/cli.py`
- Create: `tests/test_plugin_background.py`
- Modify: `tests/test_scheduler.py`

**Interfaces:**

```python
class BackgroundScheduler:
    # runtime_factory receives the submit-time plugin snapshot
    runtime_factory: Callable[[tuple[str, ...]], BackgroundRuntime]
    def submit(
        self,
        record: SessionRecord,
        task: str,
        sensitive_values: tuple[str, ...],
        manual_skill_names: tuple[str, ...] = (),
        enabled_plugin_names: tuple[str, ...] = (),
    ) -> BackgroundJob: ...

class PluginManager:
    def load_snapshot(self, names: tuple[str, ...]) -> tuple[PluginInfo, ...]: ...
```

- [x] Write RED tests showing submit captures enabled names, a queued/running worker uses an isolated registry, later foreground enable/disable cannot pollute it, snapshot loading does not rewrite global state, and a missing/broken snapshotted plugin leaves built-ins and job startup usable with diagnostics.
- [x] Run Task 4 tests and confirm the old zero-argument runtime factory cannot satisfy snapshot behavior.
- [x] Extend job state and runtime factory minimally, pass the immutable snapshot from `/background`, and construct each worker with six built-ins plus `load_snapshot`. Update existing scheduler callers without adding AgentRunner branches or shared registries.
- [x] Run scheduler/background/plugin tests and full suite; perform one review of race avoidance, lifecycle cleanup, backward behavior, and failure isolation; fix once and rerun.

### Task 5: Constrained git-readonly demo plugin

**Files:**
- Create: `examples/plugins/git-readonly/plugin.json`
- Create: `examples/plugins/git-readonly/plugin.py`
- Create: `tests/test_git_readonly_plugin.py`

**Interfaces:**

```python
def get_tools(context: PluginContext) -> tuple[RegisteredTool, ...]:
    # names: git_status, git_diff, git_log
```

- [x] Write fake-subprocess RED tests for fixed workspace cwd, argv with `shell=False`, bounded timeout/output, stdout/stderr and nonzero results, missing Git, timeout, staged/unstaged diff, safe optional relative paths, `max_count` integer `1..20`, filtered credential-shaped environment names, and absence of arbitrary-argument/mutation capabilities.
- [x] Write a real temporary-repository integration test. Test setup may run fixed `git init`, local test-only identity configuration, add, and commit only inside `tmp_path`; plugin execution must call only status/diff/log and must not mutate repository state.
- [x] Run the git plugin tests and confirm failure because the demo package is absent.
- [x] Implement three `RegisteredTool` values using subprocess argv only. Convert timeout, missing executable, nonzero status, and unexpected runner errors to bounded structured ToolResults without exception/credential leakage.
- [x] Run git plugin, ToolRegistry, command-tool, and full tests; perform one review of read-only policy, injection resistance, platform behavior, output/error semantics, and demo clarity; fix once and rerun.

### Task 6: Agent E2E, broken-plugin recovery, demo guide, and final verification

**Files:**
- Create: `tests/test_plugin_system_e2e.py`
- Create: `docs/plugin-demo.md`
- Modify: `README.txt`
- Modify: `tests/test_readme.py`
- Modify: `docs/superpowers/plans/2026-08-29-plugin-system-final-plan.md`

**Required E2E:**

```text
disabled -> UNKNOWN_TOOL
enable git-readonly
FakeModelClient -> git_status -> git_diff -> built-in read_file -> final
disable -> plugin definitions absent on next turn
broken plugin import -> sanitized diagnostic; valid plugin and six built-ins remain
restart -> explicit enabled state restored
```

- [x] Write one deterministic Plugin Agent E2E with temporary plugin home/workspace/Git repository, real PluginManager, real ToolRegistry, six built-ins, FakeModelClient, and unmodified AgentRunner. Add a separate broken-plugin recovery assertion.
- [x] Run the E2E RED and confirm plugin lifecycle/integration is the missing behavior.
- [x] Add a repeatable two-minute PowerShell demo guide covering trusted plugin copy, initial disabled list, enable, plugin+built-in Agent task, disable, trace expectations, and cleanup. Keep `README.txt` within 1,000 characters while stating in-process trust/no sandbox and why constrained tools add value beyond `execute_command`.
- [x] Run the E2E and targeted plugin suites; perform the Task's one review of demonstration credibility, requirement coverage, architecture freeze questions, docs safety, and scope; apply one necessary fix wave.
- [x] Perform exactly one final inline review. Then run targeted Plugin/registry/CLI/git/E2E tests, full pytest, compileall, CLI help, command smokes, temporary Git demo, Context/Memory/Skill/six-tools/original-demo regressions, forbidden-framework/dependency scan, credential scan, and current-wave placeholder scan. Record exact evidence and stop for optional Live DeepSeek Plugin E2E if it is the only remaining uncertainty.

## Owner-managed Git checkpoints

Do not execute Git writes. Record suggested checkpoints after Tasks 2, 4, and 6, then continue without pausing. The final report supplies recommended file groups and commit messages for the owner.

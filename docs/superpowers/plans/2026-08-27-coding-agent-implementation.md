# Coding Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not begin execution until the project owner approves this plan. Git write operations remain owner-only.

**Goal:** Build a compact Python CLI coding agent whose self-implemented loop can call an OpenAI-compatible model, dispatch six local tools, feed results back to the model, and terminate predictably across success and failure paths.

**Architecture:** A synchronous modular monolith separates protocol types, deterministic conversation/context management, the model-client interface, AgentRunner, the tool registry, local tools, one OpenAI-compatible adapter, and CLI composition. All core paths are tested offline through a scripted FakeModelClient; the live provider is an edge adapter, not the owner of agent behavior.

**Tech Stack:** Python 3.11+, standard library, `httpx` as the only runtime third-party dependency, `pytest` as the test dependency, OpenAI-compatible Chat Completions tool calling.

**Spec:** `docs/superpowers/specs/2026-08-27-coding-agent-design.md`

## Global Constraints

- The project PDF remains the highest-priority binding requirement source.
- Do not use or wrap an existing coding agent, Agent Framework, or Agent SDK.
- Conversation/history management, context selection, tool definitions, dispatch, local execution, provider response parsing, result feedback, the Agent Loop, termination, maximum-step protection, and error handling must be implemented in this repository.
- The runtime is Python 3.11+, synchronous, single-process, and a modular monolith.
- MVP supports one OpenAI-compatible provider adapter and native Tool Calling; it does not support multiple providers simultaneously.
- MVP provides exactly six local tools: `list_files`, `search_text`, `read_file`, `write_file`, `replace_in_file`, and `execute_command`.
- Context always retains the system prompt and original user task, retains the most recent 8 complete model turns by default, truncates each tool output to 20,000 characters, and uses an 80,000-character total budget by default.
- Do not add action journal, model-generated summaries, long-term memory, Web UI, database, multi-Agent behavior, plugin system, Docker sandbox, complex patch engine, streaming, or parallel tool execution.
- `FINAL_RESPONSE` means protocol-level termination only; it must never be represented as proof of semantic task correctness.
- `execute_command` uses the workspace root as its fixed default cwd, filters the configured provider API-key variable and explicitly listed sensitive variables, has a 30-second default timeout, and enforces a 120-second hard maximum.
- API keys are accepted only through environment variables and must not appear in the repository, README, logs, tests, or Demo.
- All normal automated tests must run without a network connection or paid API.
- Use TDD for every behavior: observe the relevant test fail, implement the minimum behavior, then observe it pass.
- Codex must not run `git init`, `git add`, `git commit`, `git push`, `git pull`, `git merge`, `git rebase`, `git reset`, branch-changing commands, `git tag`, or Git configuration writes. At each checkpoint, report suggested files and a commit message to the project owner.

---

## Planned File Map

```text
pyproject.toml                         # Package metadata, dependencies, pytest configuration
src/coding_agent/__init__.py          # Package marker and public version
src/coding_agent/__main__.py          # `python -m coding_agent` entry point
src/coding_agent/protocol.py          # Domain dataclasses, enums, result invariants, serialization
src/coding_agent/model.py             # ModelClient Protocol and provider-facing exceptions
src/coding_agent/context.py           # ConversationHistory, ContextManager, deterministic truncation
src/coding_agent/agent.py             # Explicit AgentRunner loop and event emission
src/coding_agent/config.py            # CLI/environment configuration resolution and validation
src/coding_agent/system_prompt.py     # Short coding-agent behavioral policy
src/coding_agent/cli.py               # Argument parsing, dependency composition, exit-code mapping
src/coding_agent/providers/__init__.py
src/coding_agent/providers/openai_compatible.py
src/coding_agent/tools/__init__.py     # Default six-tool registry composition
src/coding_agent/tools/registry.py     # Registration, argument parsing/validation, dispatch
src/coding_agent/tools/paths.py        # Workspace path containment checks
src/coding_agent/tools/files.py        # Five file/list/search tools
src/coding_agent/tools/command.py      # Local command tool
tests/fakes.py                         # Scripted FakeModelClient and reusable fake tools
tests/test_protocol.py
tests/test_context.py
tests/test_tool_registry.py
tests/test_list_files.py
tests/test_text_tools.py
tests/test_file_mutation_tools.py
tests/test_command_tool.py
tests/test_agent.py
tests/test_openai_compatible.py
tests/test_config.py
tests/test_cli.py
tests/test_end_to_end.py
tests/test_readme.py
demo/buggy_project/duration.py         # Intentionally buggy, isolated Demo target
demo/buggy_project/test_duration.py    # Tests that expose the Demo bug
demo/README.txt                        # Reset/recording instructions; no credentials
README.txt                             # Submission-constrained project instructions
```

`pyproject.toml` must set pytest `testpaths = ["tests"]` so the intentionally failing Demo fixture is not collected by the project test suite.

## Scope Check

The spec contains several components, but they are not independent products: protocol types, context, dispatcher, local tools, provider, and CLI form one vertical Agent Loop and share the same acceptance Demo. One implementation plan is therefore appropriate. The task boundaries below remain independently reviewable and testable without creating separate architectures or expanding MVP scope.

---

### Task 1: Package Foundation and Protocol Types

**Files:**

- Create: `pyproject.toml`
- Create: `src/coding_agent/__init__.py`
- Create: `src/coding_agent/protocol.py`
- Create: `tests/test_protocol.py`

**Interfaces:**

- Produces: `Role`, `ToolCall`, `ToolDefinition`, `Message`, `ToolResult`, `ModelTurn`, `RunStatus`, `RunResult`, `AgentEvent`.
- Enforces: every successful `ToolResult` has no error fields; every failed `ToolResult` has both `error_code` and `error_message`.

- [ ] **Step 1: Create package metadata and the failing protocol tests**

Use this dependency and test configuration in `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "coding-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27,<1"]

[project.optional-dependencies]
test = ["pytest>=8,<9"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Start `tests/test_protocol.py` with concrete invariants:

```python
import pytest

from coding_agent.protocol import ToolResult


def test_success_result_rejects_error_fields() -> None:
    with pytest.raises(ValueError, match="successful ToolResult"):
        ToolResult("call-1", "read_file", True, "text", "IO_ERROR", "bad")


def test_failure_result_requires_code_and_message() -> None:
    with pytest.raises(ValueError, match="failed ToolResult"):
        ToolResult("call-1", "read_file", False, "", "FILE_NOT_FOUND", None)


def test_failure_result_preserves_output_code_and_message() -> None:
    result = ToolResult(
        "call-1",
        "execute_command",
        False,
        "stdout before failure",
        "COMMAND_FAILED",
        "command exited with code 2",
    )
    assert result.output == "stdout before failure"
    assert result.error_code == "COMMAND_FAILED"
    assert result.error_message == "command exited with code 2"
```

- [ ] **Step 2: Run the tests and observe the missing protocol module**

Run: `python -m pytest tests/test_protocol.py -q`

Expected: FAIL during collection because `coding_agent.protocol` or `ToolResult` does not exist.

- [ ] **Step 3: Implement the protocol types and invariants**

Use frozen, slotted dataclasses. The public shapes must be exactly:

```python
class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    ok: bool
    output: str = ""
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("successful ToolResult cannot contain error fields")
        if not self.ok and (not self.error_code or not self.error_message):
            raise ValueError("failed ToolResult requires error_code and error_message")

    def as_message_content(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "output": self.output,
                "error_code": self.error_code,
                "error_message": self.error_message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
```

Also define `ModelTurn(final_text, tool_calls)`, `RunStatus` with the five spec values, `RunResult(status, final_text, steps, error)`, and `AgentEvent(kind: str, step: int, message: str)` used only for observable CLI events.

- [ ] **Step 4: Run targeted and full tests**

Run: `python -m pytest tests/test_protocol.py -q`

Expected: all protocol tests PASS.

Run: `python -m pytest -q`

Expected: all currently collected tests PASS; `demo/` is not collected.

- [ ] **Step 5: Stop for the owner-managed commit checkpoint**

Report completed behavior and suggest including `pyproject.toml`, `src/coding_agent/__init__.py`, `src/coding_agent/protocol.py`, and `tests/test_protocol.py`.

Suggested commit message: `chore: establish package and protocol types`

---

### Task 2: Deterministic Conversation and Context Management

**Files:**

- Create: `src/coding_agent/context.py`
- Create: `tests/test_context.py`
- Modify: `src/coding_agent/protocol.py` only if a serialization helper is required by the tests

**Interfaces:**

- Consumes: `Message`, `Role`, `ToolResult` from Task 1.
- Produces: `ConversationHistory(system_prompt: str, original_user_task: str)`, `ContextManager(max_context_chars=80000, recent_turns=8, max_tool_output_chars=20000)`, `ContextBudgetError`, and `truncate_text(text, limit)`.
- Produces: `ContextManager.prepare_tool_result(result) -> ToolResult` and `ContextManager.build(history) -> tuple[Message, ...]`.

- [ ] **Step 1: Write failing tests for permanent anchors and recent complete turns**

Use assistant messages as turn boundaries and include all immediately following tool messages in the same turn:

```python
def test_build_keeps_anchors_and_only_recent_complete_turns() -> None:
    history = ConversationHistory("system", "original task")
    history.append(Message(Role.ASSISTANT, tool_calls=(ToolCall("1", "x", "{}"),)))
    history.append(Message(Role.TOOL, "old result", tool_call_id="1"))
    history.append(Message(Role.ASSISTANT, "new final"))

    messages = ContextManager(recent_turns=1).build(history)

    assert [message.content for message in messages] == [
        "system",
        "original task",
        "new final",
    ]
```

Add tests proving that an assistant tool-call message and all of its tool results are either retained together or removed together.

- [ ] **Step 2: Write failing tests for deterministic output truncation and total budget**

```python
def test_truncate_text_counts_marker_inside_limit() -> None:
    result = truncate_text("A" * 40 + "Z" * 40, 50)
    assert len(result) == 50
    assert result.startswith("A")
    assert result.endswith("Z")
    assert "output truncated" in result


def test_prepare_tool_result_preserves_error_fields() -> None:
    source = ToolResult("1", "cmd", False, "x" * 100, "COMMAND_FAILED", "exit 2")
    result = ContextManager(max_tool_output_chars=40).prepare_tool_result(source)
    assert len(result.output) == 40
    assert result.error_code == "COMMAND_FAILED"
    assert result.error_message == "exit 2"
```

Add a test where anchors alone exceed the budget and assert `ContextBudgetError`, plus a test that old turns are removed oldest-first until the deterministic serialized size is within budget.

- [ ] **Step 3: Run the context tests and observe failure**

Run: `python -m pytest tests/test_context.py -q`

Expected: FAIL because `ConversationHistory`, `ContextManager`, and `truncate_text` do not exist.

- [ ] **Step 4: Implement history, turn grouping, truncation, and budget selection**

Implement the critical API as follows:

```python
class ConversationHistory:
    def __init__(self, system_prompt: str, original_user_task: str) -> None:
        self._messages = [
            Message(Role.SYSTEM, system_prompt),
            Message(Role.USER, original_user_task),
        ]

    def append(self, message: Message) -> None:
        self._messages.append(message)

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)


class ContextManager:
    def __init__(
        self,
        max_context_chars: int = 80_000,
        recent_turns: int = 8,
        max_tool_output_chars: int = 20_000,
    ) -> None:
        if min(max_context_chars, recent_turns, max_tool_output_chars) <= 0:
            raise ValueError("context limits must be positive")
        self.max_context_chars = max_context_chars
        self.recent_turns = recent_turns
        self.max_tool_output_chars = max_tool_output_chars
```

`truncate_text` must compute the final marker first, subtract its length from the limit, split the remaining capacity between head and tail, and give an odd extra character to the tail. `build` must serialize candidate messages with `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` for deterministic size estimation. It must never mutate canonical history and must not contain an action-journal or summary branch.

- [ ] **Step 5: Run context and full tests**

Run: `python -m pytest tests/test_context.py -q`

Expected: all context tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/context.py`, `tests/test_context.py`, and `src/coding_agent/protocol.py` only if actually changed.

Suggested commit message: `feat: add deterministic context management`

---

### Task 3: Tool Registry, Argument Validation, and Structured Dispatch Errors

**Files:**

- Create: `src/coding_agent/tools/__init__.py`
- Create: `src/coding_agent/tools/registry.py`
- Create: `tests/test_tool_registry.py`

**Interfaces:**

- Consumes: `ToolCall`, `ToolDefinition`, `ToolResult`.
- Produces: `ToolArgumentError`, `RegisteredTool`, `ToolRegistry.register()`, `ToolRegistry.definitions()`, `ToolRegistry.dispatch()`.
- Handler signature: `Callable[[str, dict[str, Any]], ToolResult]`, where the first argument is the tool-call ID.
- Validator signature: `Callable[[dict[str, Any]], dict[str, Any]]`; it returns normalized arguments or raises `ToolArgumentError`.

- [ ] **Step 1: Write failing dispatch tests**

Cover success, unknown tool, invalid JSON, non-object JSON, missing fields, unknown fields, wrong types, duplicate registration, and unexpected handler exceptions. A representative recovery assertion is:

```python
def test_unknown_tool_returns_recoverable_error() -> None:
    result = ToolRegistry().dispatch(ToolCall("c1", "missing", "{}"))
    assert result.ok is False
    assert result.output == ""
    assert result.error_code == "UNKNOWN_TOOL"
    assert "missing" in result.error_message
```

Use a fake `echo` registered tool to assert that parsed and normalized arguments reach the handler and that `ToolResult.tool_call_id` matches the model call ID.

- [ ] **Step 2: Run the registry tests and observe failure**

Run: `python -m pytest tests/test_tool_registry.py -q`

Expected: FAIL because the registry module does not exist.

- [ ] **Step 3: Implement the registry with explicit failure conversion**

Use these exact registration boundaries:

```python
@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    handler: Callable[[str, dict[str, Any]], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        if tool.definition.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.definition.name}")
        self._tools[tool.definition.name] = tool

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())
```

`dispatch` must parse `arguments_json` with `json.loads`, require `dict`, look up the tool, run validation, then invoke the handler. It must return:

- `UNKNOWN_TOOL` for an absent name;
- `MALFORMED_ARGUMENTS` for JSON/shape/validator errors;
- `TOOL_INTERNAL_ERROR` for an unexpected handler exception, with a concise message and no traceback;
- the handler's own valid `ToolResult` otherwise.

Add a reusable `require_keys(arguments, required, optional)` helper that rejects missing and unknown keys. Do not implement a general JSON Schema engine.

- [ ] **Step 4: Run registry and full tests**

Run: `python -m pytest tests/test_tool_registry.py -q`

Expected: all registry tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/__init__.py`, `src/coding_agent/tools/registry.py`, `tests/test_tool_registry.py`.

Suggested commit message: `feat: add tool registry and structured dispatch errors`

---

### Task 4: FakeModelClient and Explicit Agent Loop

**Files:**

- Create: `src/coding_agent/model.py`
- Create: `src/coding_agent/agent.py`
- Create: `tests/fakes.py`
- Create: `tests/test_agent.py`

**Interfaces:**

- Produces: `ModelClient.complete(messages, tool_definitions) -> ModelTurn` Protocol.
- Produces: `ModelClientError`, `ModelTransportError`, `ModelProtocolError`.
- Produces: `AgentRunner(model_client, registry, context_manager, max_steps=20, event_sink=None)` and `run(system_prompt, original_user_task) -> RunResult`.
- Produces: test-only `FakeModelClient(script)` that records calls and returns scripted turns or raises scripted exceptions.

- [ ] **Step 1: Write the failing FakeModelClient and normal-final tests**

```python
def test_nonempty_final_is_protocol_level_termination() -> None:
    model = FakeModelClient([ModelTurn("finished", ())])
    runner = AgentRunner(model, ToolRegistry(), ContextManager())

    result = runner.run("system", "task")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert result.final_text == "finished"
    assert result.steps == 1
```

Add a test proving an empty final with no tool calls returns `MODEL_ERROR`, not `FINAL_RESPONSE`.

- [ ] **Step 2: Write failing multi-step, feedback, max-step, stall, and model-error tests**

Script a model sequence that calls a fake tool, inspects the next request's tool message JSON, then returns final text. Assert that the JSON includes all four result values: `ok`, `output`, `error_code`, and `error_message`.

Add tests for:

- an unknown tool result followed by model recovery;
- a response containing both text and tool calls, where tools take precedence over termination;
- multiple tool calls executed in response order;
- `max_steps=2` returning `MAX_STEPS` after two tool-calling model turns;
- three consecutive identical failing calls returning `STALLED`;
- `ModelClientError` returning `MODEL_ERROR`;
- an unexpected local exception returning `INTERNAL_ERROR` without a traceback in `RunResult.error`;
- emitted events containing step, requested tool name, result status, and final protocol status.

- [ ] **Step 3: Run the agent tests and observe failure**

Run: `python -m pytest tests/test_agent.py -q`

Expected: FAIL because `ModelClient`, `FakeModelClient`, and `AgentRunner` do not exist.

- [ ] **Step 4: Implement ModelClient, FakeModelClient, and the explicit loop**

The production interface is:

```python
class ModelClient(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        tool_definitions: Sequence[ToolDefinition],
    ) -> ModelTurn:
        ...
```

The fake stores `calls: list[tuple[tuple[Message, ...], tuple[ToolDefinition, ...]]]` and pops exactly one scripted item per `complete` invocation. Exhausting the script raises an assertion error so tests cannot silently invent behavior.

Implement `AgentRunner.run` as the spec's `for step in range(1, max_steps + 1)` loop. For every tool result:

1. call `registry.dispatch`;
2. call `context_manager.prepare_tool_result`;
3. serialize with `ToolResult.as_message_content()`;
4. append a `Role.TOOL` message with the matching call ID;
5. update the consecutive-failure fingerprint `(name, arguments_json, error_code, error_message, output)`;
6. emit a concise event.

Reset the stall counter after any successful result or different fingerprint. Catch only `ModelClientError` as `MODEL_ERROR`; convert other unexpected exceptions at the outer run boundary to `INTERNAL_ERROR`.

- [ ] **Step 5: Run agent and full tests**

Run: `python -m pytest tests/test_agent.py -q`

Expected: all Agent Loop and FakeModel tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/model.py`, `src/coding_agent/agent.py`, `tests/fakes.py`, `tests/test_agent.py`.

Suggested commit message: `feat: implement deterministic agent loop`

---

### Task 5: Workspace Path Guard and `list_files`

**Files:**

- Create: `src/coding_agent/tools/paths.py`
- Create: `src/coding_agent/tools/files.py`
- Create: `tests/test_list_files.py`

**Interfaces:**

- Produces: `WorkspacePaths(root: Path)`, `resolve_existing(relative_path)`, `resolve_new_file(relative_path)`, and `display_path(path)`.
- Produces: `create_list_files_tool(paths: WorkspacePaths) -> RegisteredTool`.

- [ ] **Step 1: Write failing containment tests**

Use `tmp_path` to assert:

- root is resolved once;
- a normal relative file resolves inside root;
- absolute paths fail with `PATH_OUTSIDE_WORKSPACE`;
- `../outside.txt` fails;
- an existing symlink from the workspace to an outside path fails;
- a new file whose resolved parent is outside fails.

When symlink creation is unavailable on Windows, skip only that one test with the actual `OSError` reason.

- [ ] **Step 2: Write failing list-files tests**

Create nested files in an unsorted order and assert exact output lines:

```text
[D] package/
[F] package/a.py
[F] package/z.py
```

Assert recursion, POSIX-style relative display paths, ignored `.git`, `.venv`, `node_modules`, and `__pycache__` directories, the 500-entry cap, missing path failure, and file-instead-of-directory failure.

- [ ] **Step 3: Run the tests and observe failure**

Run: `python -m pytest tests/test_list_files.py -q`

Expected: FAIL because path guards and `list_files` do not exist.

- [ ] **Step 4: Implement resolved-path containment and deterministic listing**

Containment must compare `candidate.resolve(strict=False)` against `root.resolve()` with `Path.is_relative_to`. Reject absolute input before joining. Existing paths use `resolve(strict=True)`; new paths require an existing resolved parent inside root.

Define the tool schema with `additionalProperties: false`:

```python
ToolDefinition(
    name="list_files",
    description="Recursively list files and directories inside the workspace.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "default": "."}},
        "additionalProperties": False,
    },
)
```

Walk recursively without following directory symlinks, sort by normalized relative path, render directories with a trailing slash, and stop after 500 entries with a deterministic truncation line.

- [ ] **Step 5: Run targeted and full tests**

Run: `python -m pytest tests/test_list_files.py -q`

Expected: all path and listing tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/paths.py`, `src/coding_agent/tools/files.py`, `tests/test_list_files.py`.

Suggested commit message: `feat: add workspace guard and file listing`

---

### Task 6: `search_text` and `read_file`

**Files:**

- Modify: `src/coding_agent/tools/files.py`
- Create: `tests/test_text_tools.py`

**Interfaces:**

- Produces: `create_search_text_tool(paths) -> RegisteredTool`.
- Produces: `create_read_file_tool(paths) -> RegisteredTool`.
- Uses: `truncate_text` for the 20,000-character output boundary.

- [ ] **Step 1: Write failing search tests**

Assert that `search_text`:

- searches a single file or directory;
- performs literal, case-sensitive matching;
- returns `relative/path:line_number:line_text` in stable path/line order;
- rejects an empty query and unknown fields as `MALFORMED_ARGUMENTS`;
- skips ignored directories, NUL-containing files, and files larger than 1 MiB;
- returns at most 100 matches and a visible truncation marker;
- returns `FILE_NOT_FOUND`, `NOT_A_FILE`, `DECODE_ERROR`, or path-containment errors with nonempty `error_message` where applicable.

- [ ] **Step 2: Write failing read-file tests**

```python
def test_read_file_uses_one_based_inclusive_lines(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = dispatch_read(tmp_path, {"path": "sample.py", "start_line": 2, "end_line": 3})
    assert result.ok is True
    assert result.output == "2: two\n3: three"
```

Also test default full-file reading, invalid line ranges, missing file, directory input, non-UTF-8 bytes, path escape, and output truncation.

- [ ] **Step 3: Run the text-tool tests and observe failure**

Run: `python -m pytest tests/test_text_tools.py -q`

Expected: FAIL because the two tools are not implemented.

- [ ] **Step 4: Implement literal search and one-based reading**

Use `Path.read_bytes()` for binary/NUL and 1 MiB checks, then strict UTF-8 decode. When the requested path is one file, invalid UTF-8 returns `DECODE_ERROR`; during directory traversal, binary, oversized, and invalid-UTF-8 files are skipped so one unrelated file does not discard valid matches from the rest of the tree. Search line-by-line with `if query in line`; do not use regex. Use the same fixed ignored-directory set as `list_files`.

The read schema must allow only:

```python
{
    "path": str,
    "start_line": int,  # optional, default 1
    "end_line": int,    # optional, inclusive
}
```

Reject booleans as integers, require positive line numbers, and require `end_line >= start_line`. A requested start beyond EOF returns a valid empty output rather than an exception, because the file exists and the range contains no lines.

- [ ] **Step 5: Run targeted and full tests**

Run: `python -m pytest tests/test_text_tools.py -q`

Expected: all text-tool tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/files.py`, `tests/test_text_tools.py`.

Suggested commit message: `feat: add text search and file reading tools`

---

### Task 7: `write_file` and `replace_in_file`

**Files:**

- Modify: `src/coding_agent/tools/files.py`
- Create: `tests/test_file_mutation_tools.py`

**Interfaces:**

- Produces: `create_write_file_tool(paths) -> RegisteredTool`.
- Produces: `create_replace_in_file_tool(paths) -> RegisteredTool`.
- Internal helper: `_atomic_write_text(target: Path, content: str) -> None`.

- [ ] **Step 1: Write failing `write_file` tests**

Assert:

- a new UTF-8 file is created with exact content;
- an existing file fails with `FILE_ALREADY_EXISTS` when `overwrite` is absent or false;
- `overwrite=true` replaces exact content;
- a missing parent directory fails and is not created;
- path escape and symlink escape fail;
- a simulated write/replace failure leaves the original file unchanged;
- success uses `error_code=None` and `error_message=None`.

- [ ] **Step 2: Write failing exact-replacement tests**

```python
def test_replace_requires_exactly_one_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    result = dispatch_replace(
        tmp_path,
        {"path": "module.py", "old_text": "value = 1", "new_text": "value = 2"},
    )
    assert result.error_code == "EDIT_TARGET_AMBIGUOUS"
    assert target.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"
```

Add the zero-occurrence `EDIT_TARGET_NOT_FOUND` case, successful single replacement, non-UTF-8 input failure, and path containment cases.

- [ ] **Step 3: Run mutation tests and observe failure**

Run: `python -m pytest tests/test_file_mutation_tools.py -q`

Expected: FAIL because the mutation tools do not exist.

- [ ] **Step 4: Implement explicit schemas and atomic writes**

`write_file` accepts only `path`, `content`, and optional boolean `overwrite=false`. `replace_in_file` accepts only `path`, `old_text`, and `new_text`; reject empty `old_text` as `MALFORMED_ARGUMENTS`.

Implement atomic replacement with a same-directory `tempfile.NamedTemporaryFile(delete=False)`, UTF-8 text write, flush, `os.fsync`, and `os.replace`. Always remove a leftover temp file in `finally`. Perform all validation and occurrence counting before creating the temp file.

Return concise success output such as `created file: package/new.py` or `replaced 1 occurrence in: package/module.py`.

- [ ] **Step 5: Run targeted and full tests**

Run: `python -m pytest tests/test_file_mutation_tools.py -q`

Expected: all mutation tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/files.py`, `tests/test_file_mutation_tools.py`.

Suggested commit message: `feat: add atomic file mutation tools`

---

### Task 8: Bounded Local `execute_command`

**Files:**

- Create: `src/coding_agent/tools/command.py`
- Create: `tests/test_command_tool.py`

**Interfaces:**

- Produces: `create_execute_command_tool(workspace_root, sensitive_env_names, default_timeout=30, max_timeout=120) -> RegisteredTool`.
- Output format always contains `exit_code`, a `stdout` section, and a `stderr` section when the process starts.

- [ ] **Step 1: Write a cross-platform command helper and failing success/failure tests**

In the test file, construct Python subprocess commands safely for the current platform:

```python
def python_command(code: str) -> str:
    parts = [sys.executable, "-c", code]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)
```

Test exit 0 and exit 7. For exit 7 assert `ok=false`, `error_code="COMMAND_FAILED"`, `error_message` mentions 7, and both stdout and stderr remain in `output`.

- [ ] **Step 2: Write failing timeout, cwd, environment, and argument tests**

Assert:

- `os.getcwd()` printed by the child resolves to the configured workspace root;
- an ordinary test environment variable is inherited;
- the configured provider key variable and names from `CODING_AGENT_SENSITIVE_ENV_NAMES` are absent;
- timeouts below 1 or above 120 are `MALFORMED_ARGUMENTS`;
- a sleeping process returns `COMMAND_TIMEOUT` and preserves any captured partial output;
- large output is deterministically truncated before it enters conversation history.

- [ ] **Step 3: Run command tests and observe failure**

Run: `python -m pytest tests/test_command_tool.py -q`

Expected: FAIL because `execute_command` does not exist.

- [ ] **Step 4: Implement subprocess execution and secret filtering**

Build `child_env = os.environ.copy()` and remove each sensitive name with `child_env.pop(name, None)`. Use `subprocess.run` with:

```python
completed = subprocess.run(
    command,
    shell=True,
    cwd=workspace_root,
    env=child_env,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=timeout_seconds,
)
```

Return `ok=true` only for exit code 0. On `TimeoutExpired`, normalize byte/string partial output and return `COMMAND_TIMEOUT`. Do not claim that terminating the direct process guarantees termination of every descendant; retain that limitation in documentation.

- [ ] **Step 5: Run targeted and full tests**

Run: `python -m pytest tests/test_command_tool.py -q`

Expected: all command tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/command.py`, `tests/test_command_tool.py`.

Suggested commit message: `feat: add bounded local command execution`

---

### Task 9: OpenAI-Compatible Provider Adapter

**Files:**

- Create: `src/coding_agent/providers/__init__.py`
- Create: `src/coding_agent/providers/openai_compatible.py`
- Create: `tests/test_openai_compatible.py`

**Interfaces:**

- Consumes: `Message`, `ToolDefinition`, `ToolCall`, `ModelTurn`, and model exceptions.
- Produces: `OpenAICompatibleClient(base_url, model, api_key, http_client=None, sleep=time.sleep)` implementing `ModelClient`, plus an idempotent `close()` method for an internally owned HTTP client.
- Sends: non-streaming `POST {base_url}/chat/completions`.

- [ ] **Step 1: Write failing request-shape tests with `httpx.MockTransport`**

Capture a request and assert:

- `Authorization: Bearer <test value>` is present in the HTTP request but never in exception text;
- `model` and `stream=false` are sent;
- tool definitions map to `{"type":"function","function":{"name", "description", "parameters"}}`;
- system, user, assistant tool-call, and tool-result messages map to the OpenAI-compatible shape;
- no server-side code-execution or file-tool field is sent.

Use an obviously fake key such as `unit-test-key`, never a real credential.

- [ ] **Step 2: Write failing response-parsing tests**

Test a final response and a tool-calling response:

```python
payload = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\":\"a.py\"}"},
            }],
        }
    }]
}
```

Assert the adapter preserves raw `arguments_json` for registry parsing. Add malformed-envelope cases for absent choices, absent message, missing call ID/name/arguments, wrong field types, and neither content nor tool calls.

- [ ] **Step 3: Write failing retry-policy tests**

Using a counting MockTransport and an injected no-op sleep, assert:

- connection/timeout exceptions and 408, 429, 500, 502, 503, 504 receive at most 3 total attempts;
- 400 and 401 receive exactly 1 attempt;
- final failure raises a project `ModelTransportError` or `ModelProtocolError` with no API key in its message.
- an internally constructed `httpx.Client` closes exactly once, while an injected test client remains owned by the caller.

- [ ] **Step 4: Run provider tests and observe failure**

Run: `python -m pytest tests/test_openai_compatible.py -q`

Expected: FAIL because the provider adapter does not exist.

- [ ] **Step 5: Implement request conversion, response parsing, and bounded retries**

Use an injected `httpx.Client` when supplied; otherwise construct one with an explicit request timeout and mark it internally owned. Retry delays are 0.25 seconds then 0.5 seconds through the injected sleep function. Parse JSON only after `response.raise_for_status()` succeeds. Convert transport/status failures to `ModelTransportError` and malformed JSON/envelopes to `ModelProtocolError`. `close()` closes only the internally owned client and is safe to call more than once.

When a response includes tool calls and nonempty content, preserve both in `ModelTurn`; AgentRunner remains responsible for prioritizing the tool calls.

- [ ] **Step 6: Run targeted and full tests**

Run: `python -m pytest tests/test_openai_compatible.py -q`

Expected: all provider tests PASS with no network access.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 7: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/providers/__init__.py`, `src/coding_agent/providers/openai_compatible.py`, `tests/test_openai_compatible.py`.

Suggested commit message: `feat: add OpenAI-compatible model client`

---

### Task 10: Runtime Configuration and System Prompt

**Files:**

- Create: `src/coding_agent/config.py`
- Create: `src/coding_agent/system_prompt.py`
- Create: `tests/test_config.py`

**Interfaces:**

- Produces: `RuntimeConfig` and `resolve_config(...) -> RuntimeConfig`.
- Produces: `SYSTEM_PROMPT: str`.
- Configuration precedence: explicit CLI value, then named environment variable; base URL and model are required.

`RuntimeConfig` fields are fixed as `workspace: Path`, `base_url: str`, `model: str`, `api_key: str` with `repr=False`, `api_key_env: str`, `sensitive_env_names: frozenset[str]`, `max_steps: int`, `max_context_chars: int`, `recent_turns: int`, `max_tool_output_chars: int`, and `command_timeout: int`.

- [ ] **Step 1: Write failing configuration tests**

Assert exact defaults and validation:

```python
assert config.api_key_env == "OPENAI_API_KEY"
assert config.max_steps == 20
assert config.max_context_chars == 80_000
assert config.recent_turns == 8
assert config.max_tool_output_chars == 20_000
assert config.command_timeout == 30
```

Also assert CLI-over-environment precedence, missing base URL/model/API-key value failures, workspace directory validation, `max_steps` positive validation, and comma-separated `CODING_AGENT_SENSITIVE_ENV_NAMES` parsing. The resolved sensitive set must always include the selected `api_key_env` name.

- [ ] **Step 2: Write failing system-prompt assertions**

Assert that the prompt contains requirements to inspect before editing, use only supplied tools, avoid invented results, make minimal edits, react to tool errors, run relevant validation after code changes when possible, distinguish verified and unverified claims, and avoid credentials. Assert it does not mention action journal, memory, other agents, or hosted execution.

- [ ] **Step 3: Run configuration tests and observe failure**

Run: `python -m pytest tests/test_config.py -q`

Expected: FAIL because configuration and prompt modules do not exist.

- [ ] **Step 4: Implement immutable configuration and the short prompt**

Use a frozen dataclass with resolved `Path`, strings, positive integer limits, and `frozenset[str]` sensitive names. Never store a key value in `repr`; either keep the key value outside `RuntimeConfig` or mark a dedicated field `repr=False`.

The prompt must describe `FINAL_RESPONSE` behaviorally without claiming semantic success. It must say that if tests do not exist or cannot run, the final response reports that limitation.

- [ ] **Step 5: Run targeted and full tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: all configuration and prompt tests PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/config.py`, `src/coding_agent/system_prompt.py`, `tests/test_config.py`.

Suggested commit message: `feat: add runtime configuration and agent policy`

---

### Task 11: Default Tool Composition and CLI Integration

**Files:**

- Modify: `src/coding_agent/tools/__init__.py`
- Create: `src/coding_agent/cli.py`
- Create: `src/coding_agent/__main__.py`
- Create: `tests/test_cli.py`

**Interfaces:**

- Produces: `build_default_registry(config) -> ToolRegistry`, registering exactly six tools in the documented order.
- Produces: `main(argv=None, environ=None, client_factory=OpenAICompatibleClient) -> int`.
- Exit codes: 0 `FINAL_RESPONSE`, 2 invalid CLI/configuration, 3 `MAX_STEPS`, 4 `STALLED`, 5 `MODEL_ERROR`, 6 `INTERNAL_ERROR`.

- [ ] **Step 1: Write failing registry-composition and CLI parsing tests**

Assert the registry exposes exactly:

```python
[
    "list_files",
    "search_text",
    "read_file",
    "write_file",
    "replace_in_file",
    "execute_command",
]
```

Test the invocation form `python -m coding_agent --workspace PATH --base-url URL --model MODEL "TASK"` through `main(argv, environ, client_factory)`. Assert task is required, API-key values are never accepted as an argument, and invalid configuration returns 2 with a concise stderr message.

- [ ] **Step 2: Write failing offline CLI integration tests**

Inject a FakeModelClient factory. Assert:

- a final response prints `[final] protocol status: FINAL_RESPONSE` and exits 0;
- output does not contain `task succeeded` or another semantic-success assertion;
- tool request/result events print in order;
- `MAX_STEPS`, `STALLED`, `MODEL_ERROR`, and `INTERNAL_ERROR` map to their exact exit codes;
- a fake API-key value supplied through the test environment never appears in stdout or stderr.

- [ ] **Step 3: Run CLI tests and observe failure**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because composition and CLI modules do not exist.

- [ ] **Step 4: Implement dependency composition and CLI output**

`build_default_registry` constructs one shared `WorkspacePaths`, registers the five file tools, and registers the command tool with `config.sensitive_env_names`.

`main` must:

1. parse arguments without accepting a key value;
2. resolve configuration and read the key from the configured environment name;
3. construct ContextManager, registry, provider, and AgentRunner;
4. pass `SYSTEM_PROMPT` and the original task to `run`;
5. print structured events and then the model final text separately;
6. close the provider in `finally` when it exposes a callable `close` method;
7. print only concise errors and return the documented status code.

`src/coding_agent/__main__.py` contains only:

```python
from coding_agent.cli import main


raise SystemExit(main())
```

- [ ] **Step 5: Run targeted and full tests plus CLI help**

Run: `python -m pytest tests/test_cli.py -q`

Expected: all CLI tests PASS without network access.

Run: `python -m pytest -q`

Expected: all tests PASS.

Run: `python -m coding_agent --help`

Expected: exit 0; shows workspace, base URL, model, API-key environment-name, max-step, context-budget, and command-timeout options, but no raw API-key option.

- [ ] **Step 6: Stop for the owner-managed commit checkpoint**

Suggested files: `src/coding_agent/tools/__init__.py`, `src/coding_agent/cli.py`, `src/coding_agent/__main__.py`, `tests/test_cli.py`.

Suggested commit message: `feat: wire the coding agent CLI`

---

### Task 12: End-to-End Offline Test, Demo Fixture, README, and Compliance Verification

**Files:**

- Create: `tests/test_end_to_end.py`
- Create: `demo/buggy_project/duration.py`
- Create: `demo/buggy_project/test_duration.py`
- Create: `demo/README.txt`
- Create: `README.txt`
- Create: `tests/test_readme.py`

**Interfaces:**

- Validates the complete path from scripted model turn through real registry/local tools/history and back to final response.
- Provides a deterministic Demo target isolated from the normal test suite.
- Produces the PDF-constrained `README.txt` only after a real public repository URL is available.

- [ ] **Step 1: Write the failing end-to-end Agent Loop test**

Create a temporary project with a buggy function and a verification script. Script FakeModelClient turns in this order:

1. `list_files`;
2. `read_file` for source and test content;
3. `execute_command` that returns nonzero;
4. `replace_in_file` with one exact replacement;
5. the same `execute_command` returning zero after the edit;
6. a nonempty final response.

Assert:

- all tool calls execute in order;
- the first command result contains `COMMAND_FAILED`, its exit code, and concise `error_message`;
- the edit changes only the expected text;
- the second command result is successful;
- every result is visible in the next FakeModelClient request;
- the run ends with `FINAL_RESPONSE`, without a semantic-success field.

- [ ] **Step 2: Run the end-to-end test and observe failure**

Run: `python -m pytest tests/test_end_to_end.py -q`

Expected: FAIL until any missing integration seam is corrected; do not add new architecture to make it pass. Fix only interface wiring inconsistent with the approved spec.

- [ ] **Step 3: Make the minimum integration corrections and rerun**

Allowed corrections are limited to argument wiring, result serialization, event ordering, and dependency construction in existing MVP modules. If passing requires action journal, summary memory, another provider, a seventh tool, or a framework, stop and report a design contradiction instead of expanding scope.

Run: `python -m pytest tests/test_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 4: Create the deterministic Demo fixture**

Use this intentionally buggy source in `demo/buggy_project/duration.py`:

```python
def clamp_percentage(value: int) -> int:
    """Clamp an integer percentage to the inclusive range 0..100."""
    return min(100, value)
```

Use these tests in `demo/buggy_project/test_duration.py`:

```python
from duration import clamp_percentage


def test_value_inside_range_is_unchanged() -> None:
    assert clamp_percentage(42) == 42


def test_value_above_range_is_clamped() -> None:
    assert clamp_percentage(125) == 100


def test_value_below_range_is_clamped() -> None:
    assert clamp_percentage(-5) == 0
```

`demo/README.txt` instructs the recorder to copy the fixture to a disposable directory, run `python -m pytest -q` to confirm exactly one failing test, point the agent workspace at that copy, and use this task:

```text
请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。
```

- [ ] **Step 5: Verify the Demo red state and successful expected fix manually**

Run in a disposable copy: `python -m pytest -q`

Expected before agent edit: 1 failed, 2 passed.

Apply the expected one-line implementation `return max(0, min(100, value))` only in the disposable copy, then run the same command.

Expected after edit: 3 passed. Restore or discard the disposable copy; leave the committed Demo seed intentionally buggy.

- [ ] **Step 6: Write README.txt using the actual public repository URL**

Obtain the real URL from the project owner or, after the owner configures it, through the permitted read-only command `git remote get-url origin`. If no public URL exists, stop this step and request it; do not write a fake URL.

The final README uses these concise sections. Its second line must be `Git 仓库：` followed immediately by the exact public URL supplied by the owner or returned by the read-only remote query; no sample or fake URL may be written. Construct the final text from the real `repository_url` value with this exact format, then write the rendered text rather than the format expression:

```python
readme_text = f"""项目：自实现的命令行 Coding Agent
Git 仓库：{repository_url}

运行：安装 Python 3.11+，执行 pip install -e .，通过环境变量提供 API Key，然后运行 python -m coding_agent --workspace <项目目录> --base-url <兼容接口地址> --model <模型名> \"<任务>\"。

特色：同步显式 Agent Loop；六个自行实现的本地工具；确定性上下文截断；最大步数、超时和结构化错误；FakeModelClient 可离线测试多步工具调用。FINAL_RESPONSE 仅表示协议结束，不自动证明任务语义正确。

安全：密钥只通过环境变量提供，不写入仓库、README 或视频。execute_command 不是完整 OS sandbox，请在可信或可丢弃目录中运行。
"""
```

- [ ] **Step 7: Add README compliance tests**

`tests/test_readme.py` must assert:

```python
def test_submission_readme_constraints() -> None:
    text = Path("README.txt").read_text(encoding="utf-8")
    assert len(text) <= 1000
    assert "http" in text
    assert "运行" in text
    assert "特色" in text
    assert "API Key" in text
    repository_line = next(line for line in text.splitlines() if line.startswith("Git 仓库："))
    assert repository_line.removeprefix("Git 仓库：").startswith(("https://github.com/", "https://gitee.com/"))
```

Also scan for common real-secret prefixes only through synthetic patterns; never place a real key in the test fixture.

- [ ] **Step 8: Run the full offline verification suite**

Run: `python -m pytest -q`

Expected: all collected tests PASS; `demo/buggy_project/test_duration.py` is not collected because pytest `testpaths` is restricted to `tests`.

Run: `python -m compileall -q src tests`

Expected: exit 0 with no syntax errors.

Run: `python -m coding_agent --help`

Expected: exit 0 and no raw API-key argument.

Run a read-only source scan for forbidden runtime dependencies and obvious credentials:

```text
rg -n "LangChain|LlamaIndex|OpenAI Agents SDK|Claude Agent SDK|AutoGen|CrewAI|sk-[A-Za-z0-9]" src tests README.txt pyproject.toml
```

Expected: no forbidden imports or credential-like values. Mentions in explanatory documentation must be reviewed manually rather than treated as runtime dependencies.

- [ ] **Step 9: Perform one optional live smoke test only after offline tests pass**

With a disposable workspace and an environment-provided test key, run one minimal task against the configured OpenAI-compatible endpoint. Confirm tool calls parse, no key appears in logs, command cwd is correct, and final output is labeled protocol-level. Do not put this live test in CI and do not record the terminal until sensitive output has been checked.

- [ ] **Step 10: Stop for the owner-managed commit checkpoint**

Suggested files: `tests/test_end_to_end.py`, `demo/buggy_project/duration.py`, `demo/buggy_project/test_duration.py`, `demo/README.txt`, `README.txt`, `tests/test_readme.py`, plus only the minimal existing integration files changed in Step 3.

Suggested commit message: `test: add end-to-end coverage and reproducible demo`

---

## Final Manual Compliance Gate

Before declaring the project ready for submission, the executor reports evidence for every item below to the project owner:

- All offline tests pass with exact test counts.
- The six tool names and no seventh tool are registered.
- Unknown tool, malformed arguments, missing file, edit failure, nonzero command, command timeout, API failure, malformed response, context growth, infinite-loop protection, max steps, protocol final, and model failure paths have tests.
- Provider tests use MockTransport and do not contact the network.
- FakeModelClient proves the full multi-step loop without a paid API.
- No Agent Framework, Agent SDK, wrapped coding agent, server-hosted code execution, or Files API is used.
- Context contains no action journal, model summary, or long-term memory implementation.
- No API key appears in source, tests, README, Git diff, terminal recording, or video.
- `README.txt` contains the actual repository URL and is at most 1000 characters.
- The Demo seed produces exactly one failing test before repair and all tests pass after the expected repair in a disposable copy.
- The video is MP4, at most 2 minutes and 200 MB, and shows a real inspect/fail/edit/retest flow.
- Protocol-level `FINAL_RESPONSE` and semantic correctness remain explicitly distinguished in code, tests, README, Demo narration, and interview explanation.

The implementation phase is complete only after the project owner inspects the diff and performs any desired Git commit personally.

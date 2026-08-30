# Coding Agent TUI Productization Final Design

**Date:** 2026-08-29  
**Status:** Approved-by-process implementation baseline  
**Scope:** Product/application layer and full-screen terminal UI over the frozen coding-agent core

## 1. Goals and constraints

Deliver a stable, directly usable full-screen terminal product that makes sessions, agent activity, tool calls, read-only subagents, file changes, verification, Memory, Skills, Plugins, and context state visible without exposing private reasoning or sensitive values.

The following remain authoritative and unchanged: `AgentRunner`, the explicit agent loop, provider abstraction, native tool calling, local tools and `ToolRegistry`, Context/Memory, session persistence, Skills, Plugins, read-only Subagents, protocol termination, and error handling. The TUI is an application/presentation layer; it must not become a second agent runtime.

The current one-shot command and classic line-oriented interactive mode remain compatible. Git mutation, API-key persistence, Agent Frameworks, core async rewrites, writable subagents, and new Context/Memory architectures are out of scope.

## 2. Framework choice

Use **Textual 8.x** (`textual>=8,<9`). It provides the required full-screen layout, widgets, Markdown, workers, keyboard bindings, dialogs, responsive CSS, and headless Pilot tests on Python 3.11+ and Windows. It is an MIT-licensed TUI dependency, not an Agent Framework.

The existing synchronous core runs in one Textual thread worker. UI mutations remain on Textual's application thread through thread-safe posted messages. Cancellation sets a `threading.Event`; `AgentRunner` checks it at its existing safe boundaries. No thread killing and no async core conversion are introduced.

## 3. Product architecture

```text
Textual TUI
  -> CodingAgentService (stable product facade)
       -> existing session, memory, skill, plugin, recall managers
       -> existing InteractiveSession / AgentRunner / ToolRegistry
       -> ProductEvent stream and immutable ProductState views
```

New modules:

```text
src/coding_agent/application/
    events.py          typed, redacted product events
    state.py           immutable UI view models
    commands.py        slash-command parsing and help metadata
    diagnostics.py     read-only `doctor` checks
    service.py         composition root and product facade

src/coding_agent/tui/
    app.py             Textual application and worker boundary
    widgets.py         conversation, activity, session, status widgets
    screens.py         confirmation and management/detail screens
    theme.tcss         responsive product layout
```

`CodingAgentService` owns one composed runtime and exposes only product operations:

- lifecycle: `start`, `close`, `snapshot`
- tasks: `submit_task`, `cancel_task`
- sessions: `list/search/new/switch/rename/delete`
- conversation: `get_conversation`
- Memory: `list/add/delete/clear`, candidate confirmation
- Skills: `list/use/off/clear`
- Plugins: `list/enable/disable`
- Recall: `recall`

The service reuses existing stores/managers and transactional session semantics. It does not duplicate their validation, persistence, workspace isolation, redaction, or trust rules.

## 4. Typed event contract

The TUI never parses CLI output. The application layer emits immutable `ProductEvent` values with a stable `kind`, timestamp, task/session identity, safe summary text, status, and optional typed metadata. The event vocabulary covers:

- task started, model waiting, text delta, task finished/failed/cancelled
- tool started/finished
- subagent batch/task started/finished
- session changed/list changed
- Memory/Skill/Plugin state changed
- recall result, file changes, verification result
- recoverable error and status notification

Existing `AgentEvent`, streaming text callbacks, and `SubagentEvent` are adapted directly. Tool details are derived from canonical protocol messages and structured `ToolResult` JSON, never from stdout rendering. All event fields pass the existing sensitive-value redactor before publication. Event sinks are observational: sink failure must not fail the agent run.

## 5. Product state and conversation projection

The application facade returns immutable projections rather than private core state:

- `ProductStatus`: provider/model/workspace/session, running state, context report, Memory/Skill/Plugin/Subagent counts
- `SessionView`: human-readable name first, short ID, timestamps, active/running/result state
- `ConversationItem`: user, assistant, tool activity, subagent activity, error, or system notice
- `ActivityView`: operation, target/command summary, status, bounded output, expandable detail
- `ChangeView`: path, add/modify/delete status, bounded unified diff and line counts
- `VerificationView`: command, exit/result evidence, distinctly separate from the model's completion claim

Canonical session messages remain the source of truth. The facade incrementally projects only newly appended messages/events during a turn. It does not rewrite or truncate canonical history.

File changes are reported from a bounded, workspace-contained before/after text snapshot around a foreground task. Symlinks and ignored product/cache directories are skipped. Diffs are display-only, capped per file and in total, and never invoke Git mutation. Successful and failed likely verification commands are labeled from actual `execute_command` tool results; a final model response alone is labeled only as a claim.

## 6. TUI structure and interaction

The main screen contains:

- header: product, provider/model, workspace, active session
- collapsible session sidebar: filter, active/running/result markers, new/switch/rename/delete
- scrollable conversation: Markdown user/assistant cards and compact expandable activities
- multiline `TextArea`
- compact status bar: agent state, context utilization, Memory, active Skills, enabled Plugins, Subagents

The first-run/empty view explains the active workspace and gives short task examples.

Keyboard contract:

- `Ctrl+Enter`: submit non-empty input
- `Enter`: newline in the multiline editor
- `Ctrl+C`: request cancellation while running; otherwise clear non-empty input
- `Esc`: close a modal/detail view, then focus input
- `Ctrl+N`: new session
- `Ctrl+B`: toggle sidebar
- `Ctrl+L`: toggle activity detail density
- `Ctrl+K`: command/help palette
- `Ctrl+Q`: quit, with a running-task confirmation
- `Up`/`Down` at the editor boundary: input history

Bindings appear in `/help` and the footer/palette. While a foreground task runs, task submission and destructive session switching are disabled; navigation, scrolling, details, and cancellation remain responsive.

At widths below the normal desktop layout the sidebar auto-hides. The conversation and editor remain visible at 80x24. Large histories use incremental widget mounting and bounded activity details rather than rebuilding one giant Markdown document on each stream chunk.

## 7. Slash commands and management UX

`application.commands` performs deterministic parsing into typed commands. The TUI supports:

- `/new`, `/rename <name>`, `/delete`, `/sessions`, `/session <id>`, `/session search <query>`
- `/memory`, `/memory add <text>`, `/memory delete <id>`, `/memory clear`
- `/skills`, `/skill use <name>`, `/skill off <name>`, `/skill clear`
- `/plugins`, `/plugin enable <name>`, `/plugin disable <name>`
- `/recall <query>`, `/help`

Session deletion, Memory clear, and quitting during a run require confirmation. Management screens expose concise metadata and actions. Plugin UI always displays: “Executable plugins run as trusted local code.” It never auto-enables workspace code.

## 8. Errors, diagnostics, and configuration

Normal product errors are categorized as Provider, Tool, Session, Plugin, Configuration, Cancellation, or Internal errors, with a concise recovery action and no traceback. Debug details remain bounded and redacted.

Add `coding-agent doctor`, a non-secret diagnostic command checking Python version, workspace existence/writability, provider/model configuration, credential presence only, Git availability, storage writability, discovered Skills, and Plugins. A temporary storage probe is removed immediately. The command never contacts the provider and never prints secret values.

Provider configuration continues to use the existing CLI/environment resolution. The TUI introduces no second credential or provider configuration system and persists no API key/token. `coding-agent tui` launches the full-screen product; existing `coding-agent ... "task"` and task-omitted classic interactive behavior remain unchanged for compatibility and scripting.

## 9. Testing and product iteration

All ordinary development and product E2E tests are offline and deterministic. Tests cover:

- event mapping/redaction and facade state transitions
- task submission, streaming, cancellation, failure, and transactional persistence
- session navigation and management
- command parsing and Memory/Skill/Plugin/Recall controls
- activity, Subagent, file-change, verification, and error projections
- Textual Pilot input, keyboard, modal, running-state, and 80x24/large layout behavior
- restart/resume with real temporary persistence
- a complete FakeModel product scenario using the real core, tools, facade, and UI state
- unchanged classic interactive and one-shot CLI behavior

Up to three focused product iterations are permitted. Each iteration runs the TUI tests and a scripted product scenario, reviews discoverability/activity/change/verification/error/responsiveness, fixes only high-impact issues, and reruns relevant regression.

After all offline checks pass, run one bounded real DeepSeek TUI coding smoke in a disposable workspace and, only if stable and inexpensive, one short parallel-subagent smoke. If the approved live credential mechanism is unavailable at that point, report that sole external blocker without weakening offline completion.

## 10. Security and failure boundaries

- API keys and sensitive environment values are never stored, rendered, logged, diffed, or placed in product events.
- Existing workspace containment, tool policy, Plugin trust, and read-only Subagent policy remain authoritative.
- UI/event/rendering failures do not corrupt canonical sessions.
- Cancellation never commits an incomplete foreground turn, matching `InteractiveSession` semantics.
- TUI shutdown closes provider, Plugin, scheduler, and worker resources exactly once.
- No Git write operation is implemented or invoked.

## 11. Acceptance and freeze gate

The product may be declared `TUI PRODUCT v1 FINAL` only after real evidence confirms stable launch/exit, multiline submission, responsive execution, clear Tool/Subagent/change/verification state, complete Session/Memory/Skill/Plugin management, context/status visibility, cancellation, 80x24 usability, offline product E2E, unchanged CLI/core regressions, credential safety, bounded live DeepSeek smoke, and a repeatable two-minute demo.

On success, the Product/TUI architecture is frozen. Further Agent infrastructure or cosmetic expansion is not part of this wave.

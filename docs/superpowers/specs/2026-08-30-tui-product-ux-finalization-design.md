# TUI Product UX Finalization Design

**Status:** Approved direction (the user explicitly authorized direct execution)

## Goal and boundary

Turn the existing Textual shell into a mature coding-agent interface without redesigning AgentRunner, context/memory, skills, plugins, subagents, sessions, providers, or the tool protocol. The TUI remains a projection/controller over `CodingAgentService`; CLI behavior and offline determinism remain intact.

## Considered approaches

1. **Minimal structured event enrichment plus split views — selected.** Add explicit tool ownership/activity metadata at the existing registry-to-AgentEvent-to-ProductEvent boundary, then render independent Conversation and Activity panes. This satisfies trustworthy source labels without a new event framework.
2. **Classify inside the TUI from tool names — rejected.** It is smaller, but loses plugin ownership after registration changes and violates the requirement not to guess or parse display text.
3. **Replace events with a new hierarchical event bus — rejected.** It could model every UI state directly, but would redesign stable core interfaces and exceed this finalization wave.

## Information architecture

The large layout is `Sessions | Conversation + Composer | Activity`. At 80 columns, Sessions collapses while Conversation and Activity remain distinct; users can hide/show Activity with `Ctrl+L` or the command palette.

- **ConversationPane** contains only User messages, Agent Markdown, streaming Agent text, and a small empty-state/notice surface. Tool calls, verification, changes, subagents, and failures never enter its rendered conversation or test-facing plain text.
- **ActivityPane** owns task/model state, built-in tools, plugin tools, command/verification, changes, subagent batch/children, and product errors. Rows have a typed source, status icon, compact label, optional detail, selection, and bounded scrolling. New rows auto-follow only while the user is already at the bottom; `End` resumes following.
- **SessionSidebar** shows human name first (`Untitled` when unnamed), short ID second, and an unambiguous running/completed/error state.

## Structured activity contract

`RegisteredTool` gains a small `activity_kind` field (`tool`, `command`, or `control`). `ToolRegistry` remains the authority for `source` (`builtin`, `plugin:<name>`, or `control:subagent`) and exposes immutable observation methods.

`AgentEvent` gains optional `tool_name`, `tool_source`, and `activity_kind` fields. AgentRunner fills them directly from the call and registry for tool start/result events; old three-argument construction remains valid. `ProductEvent` and `ActivityView` expose typed `ActivitySource` plus optional `tool_name`, `plugin_name`, and `parent_id`. The adapter—not the TUI—maps registry metadata to:

- `BUILTIN_TOOL`
- `PLUGIN_TOOL`
- `CONTROL_SUBAGENT`
- `COMMAND_VERIFICATION`
- `ERROR`
- `TASK`

No source decision uses formatted messages, string parsing, or a TUI-owned list of tool names. `execute_command` is registered as `command`; `delegate_tasks` is registered as `control`. Plugin tools remain ordinary tools whose registry source supplies `plugin_name`.

Canonical History stays unchanged. Snapshot activities are derived from ToolCall/ToolResult pairs plus formal registry metadata; live subagent hierarchy is display-only and grouped under the current task's batch.

## Product interaction

- Typing `/` opens deterministic suggestions from the existing command grammar. Prefixes narrow commands; `/skill ` offers `use`, `off`, and `clear`. `Tab` accepts the highlighted entry.
- `Ctrl+P` opens a lightweight command palette for New Session, Switch Session, Skills, Plugins, Memory, Recall, Toggle Activity, and Help. Palette actions call the same App actions and service methods as slash commands.
- Skills and Plugins receive focused modal managers. They show name, scope/version, status, description, and allow activate/deactivate or enable/disable. Plugin trust warning remains visible. No editor, installer, marketplace, or remote discovery is added.
- The running label advances through Working, Waiting for provider, Running tool, Parallel investigation, and Verifying based on structured events; no invented percentage is shown.
- Changed files and actual verification are Activity entries distinct from the model's final response. Details can show bounded diff/output; no Git mutation is performed.
- User-facing failures use Provider, Tool, Session, Plugin, Configuration, or Internal categories. Normal UI surfaces no traceback.

## State, safety, and failure handling

Application Service remains the only business boundary. The UI never calls AgentRunner, ToolRegistry, PluginManager, SkillRegistry, or stores directly. Event text and details keep existing credential redaction. Activity observability is display-only and never enters canonical history, summary, memory, or model context.

Modal and command failures leave the composer usable. Running tasks disable submission but retain cancellation. Session switching and destructive actions keep existing idle/confirmation rules.

## Verification strategy

All development is test-first and offline. Component tests cover typed source propagation, conversation/activity separation, hierarchy and follow behavior, session labels, suggestions, palette dispatch, Skills/Plugins direct management, input/cancel/error behavior, and 80x24/large layouts. A real `CodingAgentService` + `FakeModelClient` E2E covers built-ins, three subagents, parent edit, verification, final response, skill toggling, plugin enabling and sourced plugin activity, persistence, restart, and resume. Full pytest, compile, CLI smoke, dependency/framework scan, and credential scan close the wave. Live DeepSeek is optional only if a safe temporary credential still exists.

## Explicit non-goals

No Agent Core redesign, chain-of-thought display, shell-command framework, plugin installation/marketplace, skill editing, Web UI, database, new provider, new memory/context architecture, fake progress, or Git automation.

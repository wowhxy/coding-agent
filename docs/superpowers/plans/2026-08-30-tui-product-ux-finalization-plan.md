# TUI Product UX Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user prohibited subagent-driven development and all Git writes for this wave.

**Goal:** Finalize the Textual TUI with separate conversation/activity regions, trustworthy structured activity sources, usable management/discovery controls, and deterministic end-to-end evidence.

**Architecture:** Enrich the existing registry → AgentEvent → ProductEvent projection with optional typed metadata, then keep all UI behavior behind `CodingAgentService`. Split the display into focused widgets and add deterministic command-discovery/management screens without changing stable agent protocols or persistence.

**Tech Stack:** Python 3.11+, Textual 8.x, pytest 8.x, stdlib only beyond existing dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-tui-product-ux-finalization-design.md`

## Global constraints

- Preserve one-shot CLI, interactive CLI, AgentRunner loop, six coding tools, provider abstraction, context/memory, sessions, skills, plugins, subagents, and termination semantics.
- Tests are offline, deterministic, and use `FakeModelClient`/temporary workspaces.
- No new framework, provider, database, marketplace, skill editor, chain-of-thought UI, or Git write operation.
- Each functional batch follows RED → minimal GREEN → targeted regression → one concise review → necessary fix.

---

### Task 1: Structured activity contract and split visual regions

**Files:**
- Modify: `src/coding_agent/protocol.py`
- Modify: `src/coding_agent/tools/registry.py`
- Modify: `src/coding_agent/tools/command.py`
- Modify: `src/coding_agent/subagents/control.py`
- Modify: `src/coding_agent/agent.py`
- Modify: `src/coding_agent/application/events.py`
- Modify: `src/coding_agent/application/state.py`
- Modify: `src/coding_agent/application/changes.py`
- Modify: `src/coding_agent/application/service.py`
- Modify: `src/coding_agent/tui/widgets.py`
- Modify: `src/coding_agent/tui/app.py`
- Modify: `src/coding_agent/tui/theme.tcss`
- Test: `tests/test_tool_registry.py`
- Test: `tests/test_application_events.py`
- Test: `tests/test_application_changes.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui_activity.py`

**Interfaces:**
- `RegisteredTool(..., activity_kind: str = "tool")`
- `ToolRegistry.observation_for(tool_name: str) -> tuple[str, str] | None`
- `AgentEvent(..., tool_name: str | None = None, tool_source: str | None = None, activity_kind: str | None = None)`
- `ActivitySource` enum and `ProductEvent.source/tool_name/plugin_name/parent_id`
- `ActivityView.source/tool_name/plugin_name/parent_id`
- `activity_views(..., tool_observer: Callable[[str], tuple[str, str] | None] | None = None)`
- `ConversationPane` renders only canonical user/assistant content.
- `ActivityPane.show_snapshot(snapshot)` and `ActivityPane.apply_event(event)` render all operational activity.

- [ ] Add failing registry/AgentRunner tests proving built-in, plugin, command, and control metadata comes from registration and reaches emitted AgentEvents.
- [ ] Run the focused tests and confirm failures are missing fields/observation behavior.
- [ ] Implement minimal registry and AgentEvent enrichment, marking `execute_command` as `command` and `delegate_tasks` as `control`.
- [ ] Add failing adapter/projection tests for `BUILTIN_TOOL`, `PLUGIN_TOOL`, `CONTROL_SUBAGENT`, `COMMAND_VERIFICATION`, `ERROR`, plugin name, and parent batch metadata.
- [ ] Implement typed ProductEvent/ActivityView mapping and pass registry observation from `CodingAgentService.snapshot()`.
- [ ] Add failing Textual tests proving tool/subagent/error text is absent from ConversationPane and present with labels/status in ActivityPane; include compact/details and scroll-follow behavior.
- [ ] Split the widgets/layout and implement activity selection/details/autofollow plus `Ctrl+L` panel visibility.
- [ ] Run `python -m pytest -q tests/test_tool_registry.py tests/test_agent.py tests/test_application_events.py tests/test_application_changes.py tests/test_tui_activity.py` and then the full suite.
- [ ] Perform one concise review for source trust, protocol compatibility, redaction, view separation, and resize behavior; fix ordinary findings and rerun targeted tests.

**Owner-managed checkpoint:** suggest `feat(tui): separate conversation and structured activity views` with the files above; do not invoke Git.

---

### Task 2: Slash discovery, command palette, and direct Skills/Plugins management

**Files:**
- Modify: `src/coding_agent/application/commands.py`
- Modify: `src/coding_agent/tui/widgets.py`
- Modify: `src/coding_agent/tui/screens.py`
- Modify: `src/coding_agent/tui/app.py`
- Modify: `src/coding_agent/tui/theme.tcss`
- Test: `tests/test_application_commands.py`
- Test: `tests/test_tui_input.py`
- Test: `tests/test_tui_management.py`

**Interfaces:**
- `CommandSuggestion(value: str, description: str)` and `command_suggestions(text: str) -> tuple[CommandSuggestion, ...]`
- `SlashCommandSuggestions.update_for(text)`, `accept_highlighted() -> str | None`
- `CommandPaletteScreen` returns one stable action identifier.
- `SkillManagementScreen` and `PluginManagementScreen` receive immutable views and return requested action/name pairs; App executes them through the existing service methods and refreshes the screen.

- [ ] Add failing pure grammar tests for `/`, `/s`, `/skill `, `/plugin `, deterministic order, and unrelated/ordinary text.
- [ ] Implement suggestions from one static command catalog shared with help.
- [ ] Add failing Textual tests for suggestion visibility, narrowing, `Tab` acceptance, dismissal, multiline preservation, and no accidental submit.
- [ ] Implement the suggestion widget and Composer/App key flow.
- [ ] Add failing UI tests for `Ctrl+P` actions and Skills/Plugins list, inspect, activate/deactivate, enable/disable, state refresh, and plugin trust warning.
- [ ] Implement palette and focused management screens; route all actions through shared App helpers and `CodingAgentService`.
- [ ] Run `python -m pytest -q tests/test_application_commands.py tests/test_tui_input.py tests/test_tui_management.py` and then the full suite.
- [ ] Perform one concise review for command discoverability, focus, keyboard conflicts, trusted-plugin messaging, and duplicated business logic; fix and rerun targeted tests.

**Owner-managed checkpoint:** suggest `feat(tui): add command discovery and resource management UX` with the files above; do not invoke Git.

---

### Task 3: Session, running, verification, error, and responsive polish

**Files:**
- Modify: `src/coding_agent/application/state.py`
- Modify: `src/coding_agent/tui/widgets.py`
- Modify: `src/coding_agent/tui/app.py`
- Modify: `src/coding_agent/tui/screens.py`
- Modify: `src/coding_agent/tui/theme.tcss`
- Test: `tests/test_tui_sessions.py`
- Test: `tests/test_tui_errors.py`
- Test: `tests/test_tui_responsive.py`
- Test: `tests/test_tui_input.py`

**Interfaces:**
- `SessionView.display_name` returns the explicit name or `Untitled`.
- `ProductStatusBar.update_status(status, phase="")` renders compact and wide variants without header duplication.
- App derives a finite phase label only from ProductEvent kinds/statuses.
- User-facing exception presentation maps to Provider/Tool/Session/Plugin/Configuration/Internal categories without tracebacks.

- [ ] Add failing tests for human-first session labels, active/running/result state, header naming, filter/switch/new/rename/delete, and no duplicated fallback ID.
- [ ] Implement the minimal session/header presentation changes.
- [ ] Add failing tests for Working, Waiting for provider, Running tool, Parallel investigation, Verifying, cancellation, final, and error recovery.
- [ ] Implement structured phase/status updates and unified safe error presentation.
- [ ] Add failing 80x24 and large-layout tests for independent panels, usable composer, status content, modal bounds, conversation Markdown, and Activity scrolling.
- [ ] Adjust CSS/responsive logic and first-run empty state without hiding required controls.
- [ ] Run `python -m pytest -q tests/test_tui_sessions.py tests/test_tui_errors.py tests/test_tui_responsive.py tests/test_tui_input.py` and then the full suite.
- [ ] Perform one concise review for focus, scroll ownership, long/mixed Markdown, noisy state, failure recovery, and 80-column usability; fix and rerun targeted tests.

**Owner-managed checkpoint:** suggest `fix(tui): polish sessions status errors and responsive layout` with the files above; do not invoke Git.

---

### Task 4: Offline product E2E, documentation, and final verification

**Files:**
- Modify: `tests/test_tui_product_e2e.py`
- Modify/Create: `tests/test_tui_ux_final_e2e.py`
- Modify: `tests/tui_fakes.py`
- Modify: `docs/tui-guide.md`
- Modify: `docs/tui-demo.md`

**Interfaces:**
- E2E uses real `CodingAgentService`, real persistence, real plugin package discovery, and `FakeModelClient`; no production-only test hooks.

- [ ] Add the failing final UI E2E: launch, submit, conversation isolation, built-in activity, three child subagents, parent edit, verification, final response, Skills activate/deactivate, Plugins enable/disable, plugin tool source on the next turn, restart/resume, and session persistence.
- [ ] Make only integration fixes required by the E2E and rerun it after each fix.
- [ ] Update the TUI guide and two-minute demo with the final layout, keys, slash suggestions, palette, Skills/Plugins controls, Activity labels, and verification/changes distinction.
- [ ] Run targeted TUI tests and `python -m pytest -q`.
- [ ] Run `python -m compileall -q src tests scripts`, one-shot CLI help smoke, TUI construction smoke, original AgentRunner/tool/provider regressions, forbidden framework/dependency scan, and credential-pattern scan.
- [ ] Perform one final concise review against every acceptance item and repair only concrete defects.
- [ ] If and only if a safe temporary DeepSeek credential already exists, run one bounded Live UI smoke; otherwise record it as optional remaining evidence without blocking offline completion.

**Owner-managed checkpoint:** suggest `feat(tui): finalize product UX and offline end-to-end verification` with all wave files; do not invoke Git.

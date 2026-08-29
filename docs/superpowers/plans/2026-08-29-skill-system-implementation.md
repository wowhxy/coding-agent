# Skill System Implementation Plan

**Design source:** `docs/superpowers/specs/2026-08-29-skill-system-design.md`

**Execution:** Five sequential TDD tasks. Each task ends with targeted tests and one focused review; Tasks 2 and 4 also run the relevant regression group, and Task 5 runs the complete offline verification. Git checkpoints are recommendations only; the repository owner performs all Git writes.

## Task 1: Skill package model, strict parser, discovery, and lazy loading

**Files:** create `src/coding_agent/skills.py`, `tests/test_skills.py`.

- First add failing tests for the two-key front matter contract, bounds, Unicode, metadata-only discovery, lazy body validation, deterministic order, duplicate exclusion, workspace precedence/fallback, missing roots, unreadable paths, and file/directory/root symlink rejection.
- Implement immutable `SkillMetadata`, `Skill`, `ActiveSkill`, `SkillDiagnostic`, stable `SkillError`, and `SkillRegistry(home, workspace)` with effective metadata, safe diagnostics, and `load(name)`.
- Discovery reads only bounded metadata; activation reopens and revalidates containment, identity, UTF-8, non-empty body, and size. Do not execute package content or add dependencies.
- Verify: `python -m pytest -q tests/test_skills.py --basetemp <temp>`; review parser strictness, containment, diagnostic secrecy, and Design limits; fix once if needed.

## Task 2: Session-scoped manual activation and interactive commands

**Files:** update `src/coding_agent/skills.py`, `src/coding_agent/interactive_shell.py`; create `tests/test_skill_commands.py`; update focused interactive tests only if interfaces require it.

- First add failing tests for ordered pins keyed by session ID, `/skills`, `/skill use`, `/skill off`, `/skill clear`, unknown/unloadable Skills, three-Skill and 20,000-character limits, `/new`, `/use`, deletion, and restart non-persistence.
- Implement a small `ManualSkillState` with transactional updates and optional Skill dependencies in `InteractiveShell`, preserving existing shell construction and commands.
- No Session schema change and no automatic selection yet.
- Verify targeted Skill/interactive tests, then relevant Session and Interactive regressions; perform one review for state isolation, error safety, and backward compatibility; fix once if needed.

## Task 3: Subordinate context injection and deterministic budgeting

**Files:** update `src/coding_agent/system_prompt.py`, `src/coding_agent/context.py`, `src/coding_agent/agent.py`, `tests/test_context.py`, `tests/test_agent.py`.

- First add failing tests proving the Core System Prompt declares Skills untrusted/subordinate and unable to bypass ToolRegistry, workspace, credential, Git, or termination rules.
- Add `ContextManager.set_active_skills()` and an AgentRunner proxy. Render all active bodies into one delimited `[Subordinate Skill Guidance]` system message after the core prompt and before the original task; do not mutate canonical history.
- Preserve manual-before-automatic order and global removal order: summary, memory, oldest optional recent turns, reverse automatic Skills, reverse manual Skills. Keep core prompt, original task, and latest complete turn mandatory.
- Verify targeted context/agent tests; review authority wording, message order, canonical history, and budget invariants; fix once if needed.

## Task 4: Automatic selector and pre-turn lifecycle integration

**Files:** create `src/coding_agent/skill_selector.py`; update `src/coding_agent/skills.py`, `src/coding_agent/cli.py`, `src/coding_agent/interactive_shell.py`, `src/coding_agent/scheduler.py`; create `tests/test_skill_selector.py`; update `tests/test_cli.py`, `tests/test_multiline_input.py`, `tests/test_scheduler.py`.

- First add failing tests for a metadata-only no-tools request, strict JSON, unknown/duplicate/max-three filtering, provider/protocol/tool-call fallbacks, manual precedence, and activation failure recovery.
- Implement `SkillSelector` and activation composition. Selector traffic is a direct pre-turn control-plane call: never append it to ConversationHistory, Session Summary, or a normal turn.
- Integrate once-per-task behavior into one-shot, foreground, multiline, and background paths. Snapshot background manual names and keep existing constructors compatible when Skills are absent.
- Verify targeted selector/lifecycle tests, then relevant CLI, Interactive, Scheduler, Session, Summary, Memory, Context, Provider, and Agent regressions; perform one review for exactly-once semantics, transient-state cleanup, and no-Skill compatibility; fix once if needed.

## Task 5: Offline end-to-end proof, documentation, and final verification

**Files:** create `tests/test_skill_system_e2e.py`; update `README.md`, `tests/test_readme.py`, and package exports only where needed.

- First add failing end-to-end tests for automatic CMake-project methodology selection through the real ToolRegistry/local tools, manual use/off behavior, workspace-over-user precedence, and malformed/selector/load failure recovery.
- Keep the E2E deterministic and network-free with FakeModelClient; exercise inspect, read, failing verification, exact edit, passing verification, and final response without requiring a platform compiler.
- Document package format, roots/precedence, manual commands, automatic lifecycle, subordinate trust boundary, limits, and runtime-only manual persistence while retaining existing quick-start guidance.
- Run targeted E2E/readme tests, then one final focused code review and necessary fixes.
- Final verification: full `pytest`, `compileall`, placeholder scan, dependency/scope scan, and an offline CLI/E2E smoke test. Record exact evidence and owner-managed Git checkpoint suggestions; do not perform Git writes or optional live-provider verification without explicit credential approval.

## Self-check

- **Spec coverage:** discovery, strict parsing, lazy activation, precedence, path safety, manual and automatic lifecycle, control-plane isolation, subordinate context, limits, failure recovery, all four execution paths, E2E, docs, and regressions are assigned.
- **Architecture/interfaces:** Skill discovery remains outside AgentRunner; ContextManager renders loaded text; selector uses only `ModelClient.complete`; ToolRegistry, Provider protocol, Session schema, and six tools remain unchanged.
- **Placeholders:** no implementation decision is left as TODO/TBD; temporary test paths are runtime values, not design placeholders.
- **Scope:** no Plugin System, executable Skill, new provider/tool, dependency, database, RAG, framework, Agent Loop redesign, or unrelated context refactor is included.

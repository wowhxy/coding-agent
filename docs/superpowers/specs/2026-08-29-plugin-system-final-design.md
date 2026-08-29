# Plugin System v1 FINAL Design

## Status and scope

This document records the approved Plugin System direction. Plugin v1 is a trusted, local Python extension mechanism that adds tools to the existing `ToolRegistry`; it never replaces `AgentRunner`, the Agent Loop, Context, Memory, Skills, provider handling, or termination. Only `<CODING_AGENT_HOME>/plugins/` is executable. Workspace plugin code is never discovered or imported.

Out of scope: marketplaces, remote install/update, dependency resolution, pip installation, sandbox VMs, permission DSLs, hooks, provider/Context/Memory/Skill plugins, MCP, Agent Frameworks, multi-Agent, and Web UI.

## Package, manifest, and trust boundary

An installed package is one immediate non-symlink directory under the plugin root:

```text
<CODING_AGENT_HOME>/plugins/<package>/
├── plugin.json
└── plugin.py
```

`plugin.json` is strict UTF-8 JSON with exactly `name`, `version`, `description`, and `entrypoint`. Names use `[a-z][a-z0-9-]{0,63}`; versions and descriptions are bounded non-empty strings; `entrypoint` is a relative `.py` path. The package directory is only a location and plugin identity comes from manifest `name`, allowing duplicate-name declarations to be detected explicitly. Absolute paths, `..`, missing files, canonical escapes, and symlinks in the package/manifest/entrypoint path are rejected. Discovery reads manifests only and never imports executable code. Duplicate manifest names invalidate all colliding packages.

The trust model is explicit: user-installed plus `/plugin enable` means trusted executable extension. Enabled names persist in `<CODING_AGENT_HOME>/plugins.json`. Plugin Python runs inside the coding-agent process without an OS sandbox, so README and demo documentation instruct users to enable only trusted code.

## Runtime contract

An entrypoint exports:

```python
def get_tools(context: PluginContext) -> tuple[RegisteredTool, ...]: ...
```

`PluginContext` exposes only the canonical workspace `Path`. A plugin reuses the existing `RegisteredTool`, `ToolDefinition`, validators, handlers, and `ToolResult`; there is no second tool protocol. The manager imports only on enable/startup restore, requires a concrete tuple/list of valid `RegisteredTool` objects, validates every definition and handler first, then calls one transactional registry operation.

Tool names use provider-safe `[a-z][a-z0-9_]{0,63}`. Definitions require non-empty descriptions and a JSON-serializable object input schema. A plugin cannot collide with built-ins or another plugin. Any import, contract, validation, or collision failure rejects the whole plugin and leaves the registry unchanged.

## ToolRegistry integration

`ToolRegistry` keeps source ownership beside each tool. Existing `register(tool)` remains compatible and records `builtin`. New operations are:

```python
register_many(tools: tuple[RegisteredTool, ...], source: str) -> None
unregister_source(source: str) -> tuple[str, ...]
source_of(tool_name: str) -> str | None
```

`register_many` validates the complete batch before mutation. `unregister_source` refuses the `builtin` source. Plugin sources are `plugin:<name>`. Definitions retain deterministic insertion order; disabling removes only that plugin's tools.

## Manager, persistence, and diagnostics

`PluginManager(home, workspace, registry)` owns manifest discovery, enabled-state parsing, import/load/unload, and diagnostics. Public behavior:

```python
discover() -> tuple[PluginInfo, ...]
enable(name: str, *, persist: bool = True) -> PluginInfo
disable(name: str, *, persist: bool = True) -> PluginInfo | None
restore_enabled() -> tuple[PluginInfo, ...]
load_snapshot(names: tuple[str, ...]) -> tuple[PluginInfo, ...]
enabled_names -> tuple[str, ...]
diagnostics -> tuple[PluginDiagnostic, ...]
```

State JSON is `{ "schema_version": 1, "enabled": [sorted unique names] }`, written atomically. Repeated enable/disable is deterministic and idempotent. Startup corruption, a missing previously enabled package, an invalid manifest/entrypoint, or an import failure produces a sanitized diagnostic and the core agent continues. Missing enabled names remain in persisted state so reinstalling restores the user's prior explicit choice; explicit disable removes them.

Diagnostics have stable codes and safe messages for malformed manifest, duplicate name, missing/unsafe entrypoint, import/contract/invalid-tool/collision/enable/state failure, disabled plugin, and missing previously enabled plugin. Ordinary output never prints tracebacks, exception text, credentials, environment values, or executable source.

## CLI and runtime lifecycle

Interactive commands are:

```text
/plugins
/plugin enable <name>
/plugin disable <name>
```

`/plugins` prints `NAME VERSION STATUS DESCRIPTION`, followed by sanitized diagnostics. Foreground and one-shot modes build the six built-ins first, then restore explicitly enabled plugins. AgentRunner receives only the resulting `ToolRegistry` and contains no plugin-specific branch.

Background submission snapshots enabled plugin names. Each worker builds a fresh default registry and a fresh manager, then loads exactly that snapshot without changing persisted state. Enable/disable during a running or queued job cannot mutate another runtime's registry.

## Demonstration plugin

`examples/plugins/git-readonly/` provides `git_status`, `git_diff`, and `git_log`. It uses `subprocess` argv with `shell=False`, fixed workspace cwd, timeout, bounded output, and filtered credential-shaped environment names.

- `git_status`: fixed `git status --short`.
- `git_diff`: only `staged: bool` and an optional safe relative `path`; invokes `git diff [--cached] -- [path]`.
- `git_log`: only `max_count: int` in `1..20`; invokes `git log --oneline --max-count=N`.

No mutation tool or arbitrary Git argument is exposed. Non-repository, missing-Git, nonzero, and timeout paths return structured `ToolResult` failures.

## Testing and freeze gate

All framework tests are offline with temporary homes/workspaces and fake models. Unit tests cover manifest/path/symlink rules, lazy import, persistence/restart, diagnostics, transactional registration, collisions, idempotency, and cleanup. Git tools use fake subprocess unit tests plus a real temporary Git repository integration test. A deterministic Agent E2E proves disabled-unavailable, enable, plugin `git_status`/`git_diff`, built-in `read_file`, final response, disable, and broken-plugin isolation.

Plugin System v1 can freeze only after all fifteen requested architecture questions pass, the full pre-plugin suite remains green, dependency/credential scans are clean, and only optional Live DeepSeek tool-selection behavior remains unverified.

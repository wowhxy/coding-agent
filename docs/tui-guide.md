# Coding Agent TUI Guide

## Start

Install from the repository root:

```powershell
python -m pip install -e ".[test]"
```

Configure only the environment variable for your provider, then start from the
project you want the agent to edit:

```powershell
Set-Location D:\your-project
$env:DEEPSEEK_API_KEY = Read-Host "DeepSeek API Key"
coding-agent doctor --provider deepseek
coding-agent tui --provider deepseek
```

The current directory is the default workspace. The key is read by the existing
provider configuration and is never stored by the TUI. `doctor` checks local
readiness without contacting the provider or printing the key.

## Main workflow

- Write a coding task in the multiline editor; `Enter` inserts a newline and
  `Ctrl+Enter` submits it.
- Conversation is reserved for your messages and the Agent's Markdown response.
  Tools, commands, errors, and Subagents appear in the independent Activity pane.
- Activity labels show their real structured source: `[tool]`,
  `[plugin:<name>]`, `[subagent]`, `[command]`, `[verify]`, and `[error]`.
  Select a row and press `Enter` for bounded detail. `Ctrl+L` shows or hides the
  entire Activity pane.
- Independent `list_files`, `search_text`, and `read_file` calls from one model
  turn may overlap. Activity keeps a separate row per `tool_call_id`; completion
  is displayed without changing canonical model-feedback order.
- Changed files show `A`, `M`, or `D` plus additions/deletions. Verification is
  based on actual `execute_command` results, not merely the model's final claim.
- Continue typing to work in the same persisted session. The most recent session
  for the workspace resumes automatically after restart.
- A previously unnamed session receives a deterministic local title after its
  first protocol-complete turn. Failed or cancelled first turns remain
  `Untitled`; `/rename <name>` always creates a manual title and can be used while
  a task is running.
- Persisted conversations open at the bottom. New streamed text follows only
  while you are already at the bottom, so scrolling up to read is not interrupted.

## Keys

| Key | Action |
| --- | --- |
| `Ctrl+Enter` | Submit |
| `Enter` | Insert newline |
| `Ctrl+C` | Request cooperative cancellation, or clear idle input |
| `Esc` | Close a dialog/Session menu or focus the editor |
| `F2` | Rename the highlighted Session (Sessions list focused) |
| `Delete` | Confirm deletion of the highlighted Session (Sessions list focused) |
| `Ctrl+N` | Create a session |
| `Ctrl+B` | Toggle session sidebar |
| `Ctrl+L` | Show/hide Activity |
| `Alt+Left` / `Alt+Right` | Narrow/widen Sessions |
| `Alt+Shift+Left` / `Alt+Shift+Right` | Widen/narrow Activity |
| `Ctrl+P` | Open the command palette |
| `Ctrl+K` | Show help |
| `Ctrl+Q` | Quit; confirms first when a task is running |

At 80x24 the sidebar hides automatically while Conversation, Activity, and the
editor stay usable. Activity follows new rows while you are at the bottom; if
you scroll upward it preserves your position until you return to the end.
At ultra-narrow widths Activity also hides to protect the center conversation.
The side panes use bounded widths (Sessions 24-48, Activity 28-60), resize in
four-column steps, and restore process-local preferences when space returns.

## Commands

- Sessions: `/new`, `/rename <name>`, `/delete`, `/sessions`, `/session <id>`,
  `/session search <query>`
- Memory: `/memory`, `/memory add <text>`, `/memory delete <id>`, `/memory clear`
- Skills: `/skills`, `/skill use <name>`, `/skill off <name>`, `/skill clear`
- Plugins: `/plugins`, `/plugin enable <name>`, `/plugin disable <name>`
- History: `/recall <query>`
- Discoverability: `/help`

Typing `/` opens completion suggestions. Use `Up`/`Down` to choose and `Tab` to
accept without submitting. The `Ctrl+P` palette provides the common Session,
Skills, Plugins, Memory, Recall, Activity, pane-resizing, and Help actions.

After a successful foreground task, the model may propose at most five durable
workspace-memory candidates in one no-tools control-plane call. A deterministic
local policy checks each proposal against the current user statement or actual
successful file/config/command evidence, filters secrets and transient details,
then performs ADD, UPDATE, or IGNORE. There is no candidate approval dialog.
Successful additions and updates appear as lightweight Activity rows. Manual
`/memory add`, delete, and clear remain available, and memory never crosses a
workspace boundary.

`/skills` and `/plugins` open interactive managers. Skills show scope and
manual/automatic/inactive status and can be activated or deactivated. Plugins
show version, status, and description and can be enabled or disabled. Plugin
activity immediately carries `[plugin:<name>]`; no tool-name guessing is used.

Session names are the primary sidebar label; the short ID and working/completed/
error state are secondary. Right-click a Session for Rename, Delete, and New
Session. Rename accepts Unicode names and preserves conversation position.
Delete always confirms which Session is targeted; deleting conversation history
does not delete workspace memory. A running Session must be cancelled before it
can be deleted. The `+ New` button, `Ctrl+N`, and `/new` share the same action.

Session deletion, Memory clear, and quitting during execution require explicit
confirmation. Executable plugins run as trusted local code and are not an OS
sandbox; enable only code you trust.

## Compatibility and safety

One-shot mode remains available:

```powershell
coding-agent --provider deepseek "Run the tests and fix the failure."
```

The six local tools remain workspace-contained, but `execute_command` is not a
complete OS sandbox. Session JSON is plaintext and may contain tasks, source
excerpts, and tool output. Do not put credentials in tasks or source fixtures.
`FINAL_RESPONSE` is protocol completion, not proof of semantic correctness.

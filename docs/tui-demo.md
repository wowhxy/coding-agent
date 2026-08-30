# Two-minute TUI demo

This demo uses the real AgentRunner, ToolRegistry, local tools, provider, session
persistence, and TUI. Prepare a disposable project with one failing parser test;
never use a repository containing secrets.

## Before recording

```powershell
Set-Location D:\demo\buggy-parser
python -m pytest -q
coding-agent doctor --provider deepseek
coding-agent tui --provider deepseek
```

Keep the terminal at roughly 110x32. Use this one-line task:

> Run the failing tests, use three parallel read-only subagents to inspect the implementation, tests, and call sites, make the minimal fix, rerun the tests, and summarize the changed files and verification.

## Recording flow

1. Show the human-first workspace/session header, clean Conversation, separate
   Activity pane, status bar, and multiline editor. Type `/` briefly to show
   command discovery, or press `Ctrl+P` for the command palette.
2. Optionally open `/skills` and `/plugins` to show direct management and the
   trusted-local-code warning; do not enable an untrusted Plugin for a demo.
3. Submit with `Ctrl+Enter`. Point out `Waiting for provider`, `[tool]` activity,
   and the three indented `[subagent]` states while Conversation stays clean.
4. Show the parent-only edit, `[change]` entry, and `[verify]` test evidence.
   Select an Activity row and press `Enter` for bounded output/diff. `Ctrl+L`
   hides/shows Activity when more room is needed.
5. Point out that the final Markdown response is protocol completion while the
   passing verification is independent structured evidence.
6. Enter `/rename parser-demo`, then quit with `Ctrl+Q`.
7. Restart `coding-agent tui --provider deepseek` and show that the same workspace
   resumes the named session and conversation.

The interview explanation is the boundary: Textual calls a typed
`CodingAgentService`; the service reuses the synchronous explicit Agent Loop and
publishes redacted immutable events with registry-owned source metadata.
Conversation and Activity are separate projections. Subagents are parallel
readers, while the parent is the single writer. Verification comes from
structured ToolResult data, so a model claim cannot masquerade as a passing test.

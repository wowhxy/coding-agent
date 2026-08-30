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

1. Show the header, active workspace/session, status bar, and multiline editor.
2. Submit with `Ctrl+Enter`. Point out `Waiting for provider`, Tool activity, and
   the three visible Subagent states.
3. Show the parent-only edit and actual test verification. Press `Ctrl+L` once to
   reveal bounded detail/diff, then collapse it to keep the screen readable.
4. Point out the final Markdown response and changed-file counts.
5. Enter `/rename parser-demo`, then quit with `Ctrl+Q`.
6. Restart `coding-agent tui --provider deepseek` and show that the same workspace
   resumes the named session and conversation.

The interview explanation is the boundary: Textual calls a typed
`CodingAgentService`; the service reuses the synchronous explicit Agent Loop and
publishes redacted immutable events. Subagents are parallel readers, while the
parent is the single writer. Verification comes from structured ToolResult data,
so a model claim cannot masquerade as a passing test.

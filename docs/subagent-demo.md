# Two-minute Subagent demo

This demo shows the final architecture: a single process, three genuinely
parallel read-only child investigations, and a single writer parent. Children
reuse `AgentRunner` with only `list_files`, `search_text`, and `read_file`; the
parent alone edits files, executes `pytest`, verifies the result, and answers.

## Preparation (PowerShell)

Use a small prepared workspace containing a buggy `parser.py`, its failing
`test_parser.py`, and one call site such as `app.py`. Install this project once:

```powershell
Set-Location D:\proj
python -m pip install -e ".[test]"
```

Set `DEEPSEEK_API_KEY` in the environment or let the CLI request it with hidden
input. Never place the key in a command, README, recording, or repository.

## Recorded flow

```powershell
Set-Location D:\parser_demo
python -m pytest -q
python -m coding_agent --provider deepseek "修复 Unicode parser 失败。请用 delegate_tasks 同时安排 three 个独立只读任务，分别检查实现、测试和调用点；汇总后由父 Agent 做最小修改并重新运行 pytest。不要修改测试。"
```

Point out these stable lifecycle lines while the model is working:

```text
[subagents] batch started: 3
[subagent subagent-1] running: explore
[subagent subagent-2] running: analysis
[subagent subagent-3] running: review
[subagent subagent-1] completed: FINAL_RESPONSE
[subagents] collected: 3
```

Then show the parent-only `replace_in_file` / `execute_command` events, the
passing test output, and the final response. Finish by explaining that the
children are ephemeral, share no client/history/session, cannot write or run
commands, and return only ordered bounded results. This is intentionally not a
worktree system, multiprocess sandbox, or Agent framework.

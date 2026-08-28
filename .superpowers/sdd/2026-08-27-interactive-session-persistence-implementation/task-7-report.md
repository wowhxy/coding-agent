# Task 7: Persistence Integration, Submission Docs, and Final Regression Report

## Outcome and scope

Task 7 is complete for the repository-local implementation and documentation scope. The new disk-backed CLI integration tests passed immediately as characterization evidence, so no Task 1–6 production source was changed. README assertions were added first and produced the required RED; compact documentation then made them GREEN.

No Git operation, commit, worktree, subagent, network request, live Provider request, real API key, real user session directory, dependency, database, encryption claim, session-management UI, summary/memory feature, streaming feature, or other excluded behavior was used or added. Every session test used an existing pytest temporary workspace and a temporary `CODING_AGENT_HOME`.

## Files created or modified

- Created `tests/test_interactive_end_to_end.py`: two offline, disk-backed, process-restart-equivalent CLI integration tests.
- Modified `tests/test_readme.py`: retained every previous assertion and credential scan, and added the Task 7 interactive/persistence/plaintext/semantic-correctness requirements.
- Modified `README.txt`: compact one-shot and interactive instructions, latest-session behavior, flags, exits, plaintext warning, and semantic-correctness warning.
- Modified `demo/README.txt`: appended only an optional no-task interactive/follow-up demonstration; the primary two-minute one-shot repair Demo remains unchanged.
- Created this report.
- No file under `src/`, packaging metadata, dependency declaration, demo seed, or earlier Task 1–6 test was modified.

## Disk-backed restart characterization

The new tests use sequential `coding_agent.cli.main()` calls, the same existing temporary workspace, the same temporary `CODING_AGENT_HOME`, separate closable FakeModel clients and factories, separate injected input sequences, and a store factory that constructs real `JsonSessionStore` objects at the CLI-provided root with a deterministic UTC clock and ID generator.

Coverage includes:

- default create, one saved turn, process-equivalent restart, default latest resume, and a saved follow-up;
- exact second-request context: current `SYSTEM_PROMPT`, first user, first final assistant, latest follow-up;
- one construction, one model call, and one close for every successful one-turn client;
- same announced/resaved session ID, full two-turn JSON round trip, and workspace-index latest pointer;
- two saved sessions in one workspace through default then `--new-session`;
- proof that the newer session is latest before explicitly resuming the older ID, and proof from exact model context that explicit resume does not silently select latest;
- retention of both disk records, strict cross-workspace rejection with exit 7, and no client construction on rejection;
- absence of the current fake Provider key and system-role messages from every session JSON document.

Required first characterization command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-integration-first" tests/test_interactive_end_to_end.py
```

Exact result, exit 0:

```text
..                                                                       [100%]
2 passed in 0.37s
```

This was a legitimate characterization pass: all integration assertions were already satisfied by accepted Tasks 1–6. No production defect was invented and no production fix was made.

## README RED and GREEN

The README test retained its existing 1000-character limit, repository URL, run method, Provider/API-key/feature requirements, one-shot commands, thinking-mode assertion, and credential-like value scan. New assertions require a standalone no-task DeepSeek command, `/exit`, `Ctrl+C`, workspace-local latest resume, both session flags, a plaintext local JSON warning covering tasks/source/tool output and pasted secrets, and the statement that protocol final plus persistence do not prove semantic correctness.

After adding assertions but before changing either README:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-readme-red" tests/test_readme.py
```

Exact result, exit 1:

```text
F.                                                                       [100%]
================================== FAILURES ===================================
_____________________ test_submission_readme_constraints ______________________
>       assert "python -m coding_agent --provider deepseek" in text.splitlines()
E       assert 'python -m coding_agent --provider deepseek' in [...]
tests\test_readme.py:23: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_readme.py::test_submission_readme_constraints
1 failed, 1 passed in 0.08s
```

The intended missing behavior was the standalone no-task interactive command. This was an assertion RED, not a collection, syntax, fixture, environment, network, or credential-scan error.

After the minimal `README.txt` and `demo/README.txt` edits:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-readme-green" tests/test_readme.py
```

Exact result, exit 0:

```text
..                                                                       [100%]
2 passed in 0.02s
```

Direct review measured `README.txt` at 818 characters. Its second line remains exactly:

```text
Git 仓库：https://github.com/wowhxy/coding-agent
```

The primary Demo remains the deterministic one-shot repair story: inspect the temporary seed, observe 1 failed/2 passed, make the minimum `duration.py` repair, obtain 3 passed, and distinguish the protocol final from test-backed semantic correctness. The new interactive sequence is explicitly optional and secondary.

## Targeted regression

Required command after documentation GREEN:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-target" tests/test_interactive_end_to_end.py tests/test_readme.py tests/test_cli.py tests/test_end_to_end.py
```

Exact result, exit 0:

```text
..................................................                       [100%]
50 passed in 1.11s
```

Self-review then made the first/newer client lifecycle assertions in the two-session test explicit and added a one-model-call assertion to the shared lifecycle helper. This was test-evidence tightening only; no production behavior changed. Fresh affected verification:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-target-review" tests/test_interactive_end_to_end.py tests/test_readme.py tests/test_cli.py tests/test_end_to_end.py
```

Exact result, exit 0:

```text
..................................................                       [100%]
50 passed in 1.08s
```

## Static, help, and full verification

Required static command:

```powershell
python -m compileall -q src tests
```

Exact result: exit 0 with no stdout or stderr. The same command was rerun after self-review and again exited 0 with no output.

Required help command:

```powershell
python -m coding_agent --help
```

Exact result: exit 0. Output:

```text
usage: coding-agent [-h] [--new-session | --resume-session SESSION_ID]
                    [--workspace WORKSPACE]
                    [--provider {custom,deepseek,openai}]
                    [--base-url BASE_URL] [--model MODEL] [--api-key-env NAME]
                    [--thinking-mode {provider-default,disabled}]
                    [--max-steps MAX_STEPS]
                    [--max-context-chars MAX_CONTEXT_CHARS]
                    [--recent-turns RECENT_TURNS]
                    [--max-tool-output-chars MAX_TOOL_OUTPUT_CHARS]
                    [--command-timeout COMMAND_TIMEOUT]
                    [task]

Run one local coding-agent task, or start an interactive session when task is
omitted.

positional arguments:
  task                  coding task for one-shot mode (omit for interactive
                        mode)

options:
  -h, --help            show this help message and exit
  --new-session         create a new interactive session
  --resume-session SESSION_ID
                        resume an interactive session by ID
  --workspace WORKSPACE
                        existing workspace directory used by all local tools
                        (default: current directory)
  --provider {custom,deepseek,openai}
                        provider defaults to apply (default: custom)
  --base-url BASE_URL   OpenAI-compatible base URL (or CODING_AGENT_BASE_URL)
  --model MODEL         model name (or CODING_AGENT_MODEL)
  --api-key-env NAME    environment-variable name containing the API key
  --thinking-mode {provider-default,disabled}
                        override provider thinking mode (default: provider
                        preset)
  --max-steps MAX_STEPS
                        maximum model steps (default: 20)
  --max-context-chars MAX_CONTEXT_CHARS
                        total deterministic context budget (default: 80000)
  --recent-turns RECENT_TURNS
                        recent complete turns retained (default: 8)
  --max-tool-output-chars MAX_TOOL_OUTPUT_CHARS
                        per-tool output budget (default: 20000)
  --command-timeout COMMAND_TIMEOUT
                        default command timeout in seconds (default: 30)
```

The help command was repeated unchanged after self-review and again exited 0 with the same output.

Required full command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-interactive-final"
```

Exact result, exit 0:

```text
........................................................................ [ 22%]
..........................................................s............. [ 44%]
.........................ss............................................. [ 66%]
........................................................................ [ 88%]
....ss................................                                   [100%]
321 passed, 5 skipped in 9.01s
```

Fresh full completion gate after the self-review test tightening:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-interactive-final-review"
```

Exact result, exit 0:

```text
........................................................................ [ 22%]
..........................................................s............. [ 44%]
.........................ss............................................. [ 66%]
........................................................................ [ 88%]
....ss................................                                   [100%]
321 passed, 5 skipped in 8.06s
```

The five skips are the unchanged accepted platform/optional-tool skips. No live Provider call occurred.

## Seventeen-point Design/PDF acceptance audit

1. **No-task interactive entry — pass.** CLI/help and interactive tests prove task omission enters interactive mode.
2. **Per-turn final semantics — pass.** Accepted controller/CLI tests prove `FINAL_RESPONSE` ends only the current turn and the prompt loop continues.
3. **Default latest resume — pass.** The new restart test proves a second CLI invocation restores the same current-workspace session from disk.
4. **New session without loss — pass.** The new two-session test proves `--new-session` persists a distinct record while retaining the older JSON.
5. **Explicit, workspace-scoped resume — pass.** Exact old-history context proves explicit ID selection; another existing workspace gets exit 7 and constructs no client.
6. **Normal exits — pass.** Existing accepted tests cover exact `/exit`, blank handling, input-stage `Ctrl+C`, and EOF; README exposes `/exit` and `Ctrl+C`.
7. **Running-turn interruption policy — pass.** Existing controller tests cover rollback of the unfinished working turn and retention of the prior canonical version.
8. **Atomic persistence and secrecy — pass.** Existing store tests cover session-first/index-second atomic replacement and failures; new JSON inspection plus the credential scan prove no system role/current fake key is stored.
9. **Deterministic bounded context — pass.** Existing context tests cover permanent anchors, user-led turns, latest-turn retention, tool-pair integrity, truncation, and explicit budget failure; restart context is asserted literally.
10. **One-shot compatibility and six local tools — pass.** Targeted/full CLI and legacy end-to-end coverage remain green; the primary Demo command remains one-shot.
11. **Offline acceptance evidence — pass.** New and existing tests use FakeModelClient and pytest temporary directories only; no real Provider/API/user storage was accessed.
12. **Current system prompt on restore — pass.** The resumed request begins with exactly one current `SYSTEM_PROMPT`; disk roles exclude system.
13. **Provider-neutral complete round trip — pass.** Existing codec/store tests cover every Message/ToolCall field and pairing; new tests prove final-only multi-turn disk round trips across CLI restarts.
14. **Strict errors and exit 7 — pass.** Accepted CLI/store/controller tests cover missing/corrupt/version/workspace/save cases; the new cross-workspace path confirms concise exit 7 before client creation.
15. **Submission README — pass locally.** Python test and direct inspection prove 818 characters, preserved line-2 repository URL, run/provider/API-key/features, one-shot and interactive commands, flags/exits, plaintext warning, and no credential-like values.
16. **Protocol/persistence versus semantic correctness and Demo stability — pass locally.** README and Demo state that neither protocol final nor persistence proves correctness; the unchanged primary one-shot repair relies on the second passing test run.
17. **PDF scope and excluded-feature compliance — pass for repository-local artifacts.** `pyproject.toml` has only `httpx` as a runtime dependency (plus setuptools build and pytest test dependencies), with no Agent Framework/SDK, existing coding-agent wrapper, hosted code/file tool, database, or hosted memory. Core history/context, schemas/dispatch, local execution, parsing, termination, errors, sessions, and persistence remain project source. Credential handling and scans remain green.

## Additional PDF/submission review

- The PDF permits a Provider API client/OpenAI-compatible gateway and native tool calling; the repository continues to use its existing OpenAI-compatible adapter while owning the important agent logic.
- API-key handling remains environment/hidden-prompt based. The test fixtures are visibly fake and do not match the credential-like scan; no key appears in README or the Demo instructions.
- `README.txt` contains the repository address, run method, feature summary, and other relevant notes within the 1000-character constraint.
- The Demo instructions describe a real local programming task with deterministic failing and passing tests, keep the live key hidden, and target a two-minute narrative.
- Repository publicity/creation date/pushed history/deadline compliance, the owner-named ZIP, and the final video’s actual MP4 format, duration, size, contents, and key absence are external owner-managed submission facts. No MP4 exists in this workspace, and Git inspection was prohibited, so these cannot be certified by Task 7.
- Interview understanding and ability to defend every design decision remain an owner responsibility; the approved specs, reports, tests, and Demo notes provide the local evidence base.

## Test-quality and scope self-review

- Mutation check: wrong default selection, lost first answer, stale/missing system prompt, incorrect explicit selection, overwritten old session, absent workspace validation, wrong index latest, persisted system/key, repeated/missing client construction, extra model completion, or missing close makes at least one new integration assertion fail.
- Expected contexts and persisted messages are hand-written literal tuples, not computed with the context builder or serializer under test.
- The real CLI, real context builder, real serializer, real JSON store, real filesystem, and real workspace index are exercised. Only network/model completion and terminal input are injected.
- No assertions weaken or replace the earlier one-shot, six-tool, provider, command, schema, failure, or credential coverage.
- No production change was warranted by integration evidence. Documentation was the only behavior found missing by RED.

## Concerns

There are no known repository-local implementation or regression concerns. The five full-suite skips are unchanged. External submission artifacts and facts listed above—especially public-repository history/timing and the absent final video/ZIP—must be verified by the owner outside this no-Git, offline task.

## Git record

Commits: none. Git is owner-managed and no Git command was run.

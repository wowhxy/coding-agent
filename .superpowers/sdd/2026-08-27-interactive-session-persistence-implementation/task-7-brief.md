### Task 7: Persistence Integration, Submission Docs, and Final Regression

**Files:**
- Create `tests/test_interactive_end_to_end.py`
- Modify `tests/test_readme.py`
- Modify `README.txt`
- Modify `demo/README.txt`
- Modify Task 1–6 source/tests only when a new integration assertion proves a Design Spec gap

#### Step 1: Add disk-backed restart/resume integration tests

Use two sequential `coding_agent.cli.main()` calls with:

- the same temporary existing workspace;
- the same temporary `CODING_AGENT_HOME` mapping;
- separate closable FakeModelClient instances/factories (process-restart equivalent);
- separate injected input sequences;
- deterministic fake clock and ID generator through a store factory that creates real `JsonSessionStore` instances at the passed root;
- no network or real key.

First call: default interactive mode has no latest session, performs `first task → first answer`, then `/exit`. Second call: default mode automatically resumes, performs `follow-up → second answer`, then `/exit`. Assert:

- same session ID is announced/resaved;
- second model context is exactly current `SYSTEM_PROMPT`, first user, first final assistant, latest follow-up (subject only to existing deterministic context builder);
- each client is constructed/closed exactly once;
- session JSON round-trips both turns and contains no system role/current fake provider key;
- workspace index latest points to the session.

Add another test creating/saving two sessions in one workspace via default then `--new-session`, explicitly resuming the older ID, and proving a different workspace cannot resume it (exit 7 and no client). Assert older session is retained, explicit resume does not silently select latest, and disk JSON contains no fake key/system message.

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-integration-first" tests/test_interactive_end_to_end.py
```

If it passes immediately because Tasks 1–6 already satisfy every assertion, record it as a characterization pass and do not invent a production defect. If it fails, capture the failure and change only the smallest responsible Task 1–6 component, then rerun.

#### Step 2: Add README assertions RED, then minimal docs GREEN

Extend `tests/test_readme.py` while retaining every existing assertion and credential scan. Require `README.txt` to contain:

- the normal one-shot DeepSeek command;
- compact interactive command `python -m coding_agent --provider deepseek` without requiring a task;
- `/exit` and `Ctrl+C`;
- a statement that default interactive startup restores the latest session for the current workspace;
- `--new-session` and `--resume-session` discoverability;
- a warning that local session JSON is plaintext, may contain tasks/source/tool output, and users must not paste secrets;
- protocol final/persistence do not prove semantic correctness;
- total `len(text) <= 1000`, repository URL, run method, API-key/provider/feature requirements.

Run the README test before edits and record expected RED. Then update `README.txt` compactly. Do not place a credential-like fixture or real key in docs.

Update `demo/README.txt` without replacing or weakening the primary two-minute one-shot repair Demo. Add only a short optional secondary demonstration: run no-task interactive mode, ask one follow-up based on the prior turn, then `/exit`; explain default latest-session resume. Keep the live-demo key hidden and protocol-vs-semantic explanation.

#### Step 3: Run targeted integration/docs/regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task7-target" tests/test_interactive_end_to_end.py tests/test_readme.py tests/test_cli.py tests/test_end_to_end.py
```

#### Step 4: Run final static/help/full verification

```powershell
python -m compileall -q src tests
python -m coding_agent --help
python -m pytest -q --basetemp "$env:TEMP\coding-agent-interactive-final"
```

Record real exit/output. Do not run a live Provider request; all approved acceptance behavior is offline-testable.

#### Step 5: Self-review/report

Review all 17 Design Spec acceptance criteria, PDF constraints, README length/content, plaintext warning, system/key exclusion, two-workspace/two-session isolation, one-shot Demo stability, no excluded dependency/feature, and actual test evidence. Fix only proven gaps and repeat affected/full checks.

No Git operations. Write the complete report to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-7-report.md`, including characterization or RED evidence, README RED/GREEN, targeted/static/help/full output, files changed, compliance self-review, and concerns.

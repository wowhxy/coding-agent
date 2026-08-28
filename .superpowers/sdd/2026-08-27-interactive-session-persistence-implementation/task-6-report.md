# Task 6: CLI Session Composition and Backward Compatibility Report

## Scope and files

- Modified `src/coding_agent/cli.py` to make the positional task optional, add mutually exclusive session-selection flags, preserve the one-shot branch, and compose accepted session modules for interactive mode.
- Modified `tests/test_cli.py` with offline parser, selection, restoration, lifecycle, output, error, secrecy, and one-shot no-persistence coverage.
- Did not modify `tests/test_packaging.py`; its existing console-entry assertion remained sufficient.
- Did not modify provider, session-store, interactive-controller, packaging, end-to-end, documentation, or integration behavior outside Task 6. No Git operation, worktree, commit, network call, real provider call, or user storage was used.

## Baseline evidence

Before tests or production code were changed:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-baseline" tests/test_cli.py tests/test_packaging.py
```

Exact outcome:

```text
...................                                                      [100%]
19 passed in 0.33s
```

## RED evidence

Tests were added before production changes, then the required command was run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-red" tests/test_cli.py tests/test_packaging.py
```

Exact pytest progress and outcome:

```text
.F...FFFF............FFFFFFFFFFFFFF.                                     [100%]
19 failed, 17 passed in 0.92s
```

The failures were caused by the intended missing behavior:

- `main()` rejected the new `session_store_factory` injection with `TypeError` in the one-shot isolation and interactive composition tests.
- help still showed a required `task` and did not describe interactive mode.
- `--new-session` and `--resume-session` were unrecognized rather than mutually exclusive/config-validated session flags.
- every interactive selection, restoration, lifecycle, result-rendering, and persistence-error case failed before reaching its behavioral assertions because composition did not yet exist.

This was an expected behavioral RED, not a syntax, collection, fixture, API/network, or environment error.

## Minimal implementation

1. Raw `--api-key` scanning remains first. Argparse now accepts an optional positional task and owns mutual exclusion of `--new-session`/`--resume-session`. A post-parse mode check rejects either session flag with a one-shot task before secret prompting, config resolution, store construction, or client construction.
2. Provider preset, secret prompt, and `RuntimeConfig` resolution still happen exactly once. A present task enters the unchanged `_run_agent` path and never resolves session home or constructs a store.
3. An absent task resolves session home from the runtime environment, constructs the injected store, and selects explicit-new, explicit-ID, or strict-latest-with-create-only-on-absent-index.
4. Loaded non-system messages are restored with `ConversationHistory.from_persisted(SYSTEM_PROMPT, ...)`; empty records receive a system-only current history. Provider/model changes emit redacted warnings and remain non-fatal.
5. Interactive mode constructs one current-config client, six-tool registry, context manager, runner, and `InteractiveSession`. It injects the single API key only as a sensitive value, injected input, stderr persistence output, and an interactive result sink. The client closes in `finally` exactly once.
6. Selection/initialization `SessionError` returns 7 with `[error] CODE: concise message`; save errors retain the accepted controller's exit 7. Unexpected composition errors retain concise exit 6 behavior. Interactive final text uses `agent> ` and one-shot output retains `[response]`.

## GREEN and regression evidence

Required targeted command after the minimal production change and review coverage additions:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-target" tests/test_cli.py tests/test_interactive.py tests/test_packaging.py tests/test_end_to_end.py
```

Exact output:

```text
...........................................................              [100%]
59 passed in 1.17s
```

Required full regression command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-full"
```

Exact output:

```text
........................................................................ [ 22%]
.................................................s...................... [ 45%]
..............ss........................................................ [ 68%]
.................................................................ss..... [ 91%]
...........................                                              [100%]
310 passed, 5 skipped in 9.08s
```

## Self-review

- Validation order: raw-key rejection precedes parsing; parser/session-mode rejection precedes key prompt, config, store, input, and client. Existing raw-key tests still prove the literal value is never echoed.
- Store lifetime: one-shot mode has an injected factory that fails if called; interactive mode resolves one root and constructs one store. The API key is never passed as a root argument or placed in a record.
- Selection: default mode strictly calls `load_latest`, creates only on `None`, and propagates corrupt/missing indexed state; explicit new creates an unsaved in-memory record without deleting older files; explicit resume calls strict workspace-aware `load_session`.
- History: resumed model input begins with exactly one current `SYSTEM_PROMPT`; persisted records remain non-system-only. Explicit older-session coverage proves the requested record, rather than latest, is restored.
- Metadata drift: provider/model differences produce warning-only stderr lines, do not block a turn, and the next successful save writes the current provider/model.
- Client lifetime: one securely prompted key and one client are reused across turns. Normal exit, protocol statuses, and persistence exit 7 each close it exactly once. Selection failures construct no client.
- Rendering and compatibility: startup markers/order, tool-event sink, per-turn protocol status, `agent> ` interactive output, one-shot `[response]`, one-shot status/exit mapping, provider factory arguments, redaction, and the exact six-tool order are covered.
- Error contract: initialization/selection and save errors are concise, redacted, traceback-free exit 7; unexpected composition remains traceback-free exit 6. No session-list/delete UI, commands, summary/memory, streaming, toolkit, database, provider redesign, or unrelated refactor was added.

## Concerns

None. The five full-suite skips are pre-existing platform/optional-tool skips and are unchanged by Task 6.

## Fix round 1: reserved API-key environment collisions

### Review finding and root cause

Independent review found that `--api-key-env` could name a non-secret runtime configuration or storage variable. The selected environment entry was populated with the prompted key and then reused by `resolve_config` and `resolve_session_home`. Synthetic offline reproduction demonstrated both consequences: `CODING_AGENT_HOME` reached the injected store factory as the session root, and `CODING_AGENT_MODEL` became printed model metadata.

The root cause was the absence of a namespace-separation check between parsed key-env selection and the shared runtime environment mapping. The fix belongs before secret prompting and configuration/storage composition, not in the store or output redactor.

### Focused RED evidence

Focused tests were added first for all six reserved names, lowercase collision, the model-output path, and an arbitrary custom-name regression:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-red" tests/test_cli.py -k "reserved_api_key_env or model_api_key_env or arbitrary_custom_api_key_env"
```

Exact pytest progress and outcome:

```text
FFFFFFFF.                                                                [100%]
8 failed, 1 passed, 36 deselected in 0.45s
```

The eight failures were the expected missing validation: seven interactive reserved-name/case-fold cases continued to composition and returned 6, while the one-shot `CODING_AGENT_MODEL` case completed with exit 0 and exposed the synthetic key as model output. The arbitrary `MY_PROVIDER_SECRET` regression already passed.

### Minimal fix and files

- Modified `src/coding_agent/cli.py`: added one canonical mapping for `CODING_AGENT_BASE_URL`, `CODING_AGENT_MODEL`, `CODING_AGENT_SENSITIVE_ENV_NAMES`, `CODING_AGENT_HOME`, `LOCALAPPDATA`, and `XDG_DATA_HOME`; the selected key env is stripped, case-folded for lookup, and rejected with exit 2 and only the canonical reserved name before environment copying or secret prompting.
- Modified `tests/test_cli.py`: added focused side-effect-free rejection tests, a direct no-`resolve_config` assertion, the actual model-output exploit regression, and a normal custom key-env success regression.
- Appended this evidence to `task-6-report.md`. No other file, Git state, network, provider, real user storage, or integration surface was changed.

Raw `--api-key` scanning remains first, session-mode validation remains before key-env validation, provider defaults (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`) remain allowed, and arbitrary non-reserved names remain supported.

### GREEN and regression evidence

Focused GREEN command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-focused" tests/test_cli.py -k "reserved_api_key_env or model_api_key_env or arbitrary_custom_api_key_env"
```

Exact output:

```text
.........                                                                [100%]
9 passed, 36 deselected in 0.22s
```

Task 6 targeted command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-target" tests/test_cli.py tests/test_interactive.py tests/test_packaging.py tests/test_end_to_end.py
```

Exact output:

```text
....................................................................     [100%]
68 passed in 0.98s
```

Full regression command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-full"
```

Exact output:

```text
........................................................................ [ 22%]
..........................................................s............. [ 44%]
.......................ss............................................... [ 66%]
........................................................................ [ 88%]
..ss................................                                     [100%]
319 passed, 5 skipped in 8.51s
```

### Fix-round self-review

- Normalization uses `strip()` plus `casefold()` only for collision matching; canonical names drive the concise error, so user-supplied casing and all secret values stay out of output.
- The return-2 branch runs after raw-key and parser/mode validation but before `runtime_environment`, `secret_reader`, `resolve_config`, session-home/store, client, or input composition. Focused tests directly fail if any of those injectable boundaries are reached, including a patched `resolve_config` sentinel.
- The reserved set exactly matches the six current non-secret configuration/storage names requested; provider default key names and `MY_PROVIDER_SECRET` are covered as accepted.
- One-shot persistence isolation, interactive selection/lifecycle, six-tool composition, output/exit contracts, key redaction, packaging, and end-to-end behavior remain covered by the Task 6 target and full suite.
- Mutation check: removing any reserved entry, using case-sensitive comparison, moving validation after prompt/config, printing the supplied value, rejecting arbitrary names, or allowing `CODING_AGENT_MODEL` to become metadata fails focused coverage.

Fix-round concerns: none. The five full-suite skips remain the unchanged platform/optional-tool skips.

### Fresh completion-gate verification

After adding the explicit `resolve_config` sentinel and completing the report review, the suites were rerun with fresh basetemps:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-focused-final" tests/test_cli.py -k "reserved_api_key_env or model_api_key_env or arbitrary_custom_api_key_env"
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-target-final" tests/test_cli.py tests/test_interactive.py tests/test_packaging.py tests/test_end_to_end.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task6-fix1-full-final"
```

Exact outputs:

```text
.........                                                                [100%]
9 passed, 36 deselected in 0.28s
....................................................................     [100%]
68 passed in 0.85s
........................................................................ [ 22%]
..........................................................s............. [ 44%]
.......................ss............................................... [ 66%]
........................................................................ [ 88%]
..ss................................                                     [100%]
319 passed, 5 skipped in 8.03s
```

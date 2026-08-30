# Coding Agent v1 Final Hardening Report

Date: 2026-08-30  
Status: `CODING AGENT v1 FINAL / ARCHITECTURE FROZEN`

This report records the final offline architecture audit, bounded benchmark,
failure-injection repairs, regression evidence, and acceptance decision for
the project. Timing values are observations on one machine, not universal
performance guarantees.

## 1. Final architecture audit

The runtime remains a synchronous, single-process, modular Python monolith.
Its core coding-agent behavior is implemented in this repository:

1. CLI/TUI and `CodingAgentService` own product lifecycle and session choice.
2. `AgentRunner` owns the deterministic model/tool loop, maximum-step guard,
   protocol-level termination, cancellation checks, and tool-result feedback.
3. `ModelClient` and `OpenAICompatibleClient` isolate provider transport and
   native tool-call parsing from the loop.
4. `ToolRegistry` owns schemas, validation, dispatch, effect metadata, and
   structured errors. The six built-in coding tools remain local.
5. `ConversationHistory` and session JSON are canonical facts. Context,
   summaries, memory retrieval, and recall are bounded views or independent
   state; SQLite catalogs/FTS are rebuildable derived indexes.
6. Skills provide subordinate instructions, plugins provide explicitly loaded
   extensions, and Subagents reuse the same runner under parent-only-write and
   bounded-concurrency rules.
7. Textual TUI consumes application events and runs model work outside the UI
   event path.

No second Agent Loop, alternative history source of truth, new provider, or
framework was introduced by hardening.

## 2. Issues found and disposition

| Severity | Evidence found by failure injection | Disposition |
|---|---|---|
| P1 | A plugin tool or validator could raise `SystemExit` through the dispatch boundary and terminate the process. | Fixed in `ToolRegistry`: convert `Exception` and `SystemExit` to `TOOL_INTERNAL_ERROR`; intentionally do not catch `KeyboardInterrupt` or `GeneratorExit`. |
| P1 | A malformed provider response could reuse one tool-call ID, making ToolCall/ToolResult correlation ambiguous in both response modes. | Fixed in non-streaming and streaming parsing: reject duplicate IDs with `ModelProtocolError`. |
| P2 (high value) | If plugin shutdown raised, provider-client shutdown was skipped. | Fixed with `try/finally` lifecycle ordering so the model client is always closed. |

No benchmark justified a broad performance rewrite. In particular, scheduler
overhead dominates tiny cached local reads, so the existing explicit
parallel-safe gating was retained.

## 3. Exception and failure audit

The audit checked model transport/protocol failure, unknown or malformed tool
calls, tool exceptions and timeouts, SQLite/session corruption and rebuild,
summary/memory fallback, plugin partial load/runtime failure, child failure,
TUI worker/event failure, cancellation, and shutdown.

Boundary policy after repair:

- Provider failures become explicit model transport/protocol failures after
  bounded retry; malformed responses never enter canonical history.
- Tool failures become structured `ToolResult` values with `error_code` and
  `error_message`; one plugin cannot exit the host process.
- Control-plane failures in skill selection, summary, candidate extraction, or
  recall safely fall back without becoming normal history turns.
- Canonical session JSON remains authoritative; corrupt derived indexes can be
  rebuilt and corrupt optional state degrades safely.
- Child failures remain child results; parent execution and parent-only-write
  rules remain intact.
- Product event subscribers are isolated so one display callback cannot break
  the service.
- Cancellation is cooperative and does not commit an unfinished turn.

## 4. Bugs fixed

Three minimal production changes were made, each with a RED-to-GREEN
regression:

- `src/coding_agent/tools/registry.py`: contain plugin `SystemExit` at the tool
  validation/execution boundary.
- `src/coding_agent/providers/openai_compatible.py`: enforce unique tool-call
  IDs for normal and streamed responses.
- `src/coding_agent/application/service.py`: close the provider client even if
  plugin cleanup fails.

The repairs do not change successful tool behavior, termination semantics, or
provider request structure.

## 5. Benchmark suite

The new `benchmarks` package is bounded, deterministic, disposable, offline,
and machine-readable. Run it with:

```powershell
python -m benchmarks.run --profile smoke --output benchmark_results/smoke.json
python -m benchmarks.run --profile standard --output benchmark_results/final.json
```

It covers:

- A: Agent Loop at 1/2/5/10 steps and a multi-tool response.
- B: six-tool-compatible local execution paths and serial/parallel scheduling.
- C: three independent Subagent investigations.
- D: Context compression at 10/50/100/500 turns.
- E: Session operations at 10/100/1000 sessions.
- F: literal scan, FTS5, and rebuildable search.
- G: Workspace Memory, relevant retrieval, incremental summary, and restart.
- H: TUI startup, rendering, activity, switching, resize, and model-call audit.
- I: representative peak allocations using `tracemalloc`.
- J: eight disposable coding tasks, including Python, C++, creation, review,
  exploration, testing, and Subagents.
- Bounded stress: tools, sessions, search, cancellation, Subagents, and plugins.

Correctness assertions live in pytest. Tests do not assert fragile millisecond
thresholds.

## 6. Benchmark environment

- Platform: Windows 11 (`Windows-11-10.0.26100-SP0`)
- Python: CPython 3.13.1 (`python.exe`)
- Logical CPU count: 32
- SQLite: 3.45.3 with FTS5 available
- Provider: deterministic offline fake; no network or paid API
- Measurements: `perf_counter` elapsed time and `tracemalloc` peak allocations
- C++ task: available and completed with the installed compiler

## 7. Baseline benchmark

The pre-repair report is preserved at
`benchmark_results/baseline.json`. All ten original categories completed.
Selected observations:

- Controlled I/O tool speedup: 2.808x.
- Three-Subagent speedup: 2.573x.
- 500-turn canonical history: 649,913 characters; final Context: 4,786.
- 1,000-session list: 0.024925 s without loading session history files.
- FTS vs literal scan: 439.493x in that run.
- Disposable coding tasks: 8/8 completed and semantically verified.

The pre-change full regression baseline was `804 passed, 11 skipped`.

## 8. Final benchmark

The post-repair report is preserved at `benchmark_results/final.json`. All
eleven categories completed with zero failed scenario.

- Agent Loop: 1 step 0.001101 s; 5 steps 0.008711 s; 10 steps 0.027334 s.
- Controlled I/O tools: 0.091327 s serial vs 0.034857 s parallel, 2.620x.
- Subagents: 0.130664 s serial vs 0.048407 s parallel, 2.699x.
- 500-turn Context: 649,913 canonical characters reduced to 4,786 in
  0.057679 s while L1-L4 were exercised.
- 1,000 sessions: startup 0.000379 s, list 0.006841 s, latest resume
  0.015637 s, metadata lookup 0.000616 s; list loaded zero history files.
- FTS: 0.010640 s vs 21.397384 s literal scan, 2,010.938x in this run;
  rebuild 0.818729 s.
- Memory/Summary: incremental update processed 8 newly old messages rather
  than all 28 old messages; restart resume 0.003020 s.
- TUI: cold test-pilot start 1.177495 s, session switch 0.196636 s, resize
  0.239644 s, 40-event activity burst 3.314747 s, no crash.
- Typical simple TUI turn: one parent model call; zero selector, summary,
  candidate, and Subagent calls.
- Disposable coding tasks: 8/8 completed, 32 model calls, 26 tool calls, three
  child calls, and every semantic verifier passed.
- Bounded stress completed 50 tool calls, 40 session switches, 20 searches,
  50 cancellations, 10/30 Subagent batches/children, and 30 plugin cycles.

## 9. Before/after comparison

| Metric | Baseline | Final | Interpretation |
|---|---:|---:|---|
| Controlled tool speedup | 2.808x | 2.620x | Stable benefit under I/O wait; variance expected. |
| Subagent speedup | 2.573x | 2.699x | Parallel investigations retain a real benefit. |
| 500-turn final Context | 4,786 chars | 4,786 chars | Repairs did not change compression behavior. |
| 500-turn build | 0.054671 s | 0.057679 s | No material regression claim; normal run variance. |
| 1,000-session list | 0.024925 s | 0.006841 s | Fast-path behavior remains; absolute delta is cache-sensitive. |
| FTS speedup | 439.493x | 2,010.938x | Both runs show a large advantage; the ratio is cache-sensitive. |
| Coding tasks | 8/8 | 8/8 | Semantic behavior preserved. |

The shorter total final run and large FTS ratio change are not attributed to
the three correctness repairs. OS cache, process state, and `tracemalloc`
variance make cross-run totals unsuitable as a product speed claim.

## 10. Tool parallel speedup

Parallel scheduling gives 2.620x for three controlled I/O-wait operations.
For tiny cached real workspace reads it measured 0.622x (0.003842 s serial,
0.006176 s scheduled), demonstrating fixed scheduling overhead. This supports
the current policy: parallelize only explicitly safe independent reads and do
not claim universal speedup.

## 11. Subagent speedup

Three independent read-only investigations measured 2.699x. Children made
three model calls while the parent made none in the measured operation. The
parent context was 24,000 characters and the largest child context only 719,
confirming that full parent history was not copied into each child.

## 12. Context scaling

Canonical history intentionally remains complete and grows from 13,003 to
649,913 characters over 10 to 500 turns. The Context view stays between 4,593
and 4,786 characters. L1 trimming, L2 stale-result pruning, L3 deterministic
activity compression, and L4 persistent summary were all observed; 492 old
turns were represented compactly at 500 turns. Canonical history was not
deleted or rewritten.

## 13. Session scaling

At 1,000 sessions, startup and list use catalog/index metadata; listing loaded
zero history files and is capped at 50 displayed records. Latest-session resume
uses the recorded fast path. Rename, delete, metadata lookup, search, and recall
all completed. Session JSON remains canonical.

## 14. FTS/search scaling

At 1,000 sessions, literal fallback loaded 999 session files; FTS materialized
ten bounded results and loaded ten matching canonical files. The FTS database
was 630,784 bytes versus 623,160 bytes of synthetic canonical histories. It is
a contentless, rebuildable derived index, not a replacement source of truth.
Missing/corrupt index and FTS-unavailable fallback paths remain tested.

## 15. Memory usage

Representative `tracemalloc` peaks were bounded:

- core loop: 49,917 bytes
- tool execution: 104,909 bytes
- Subagents: 83,070 bytes
- Context scaling: 2,645,711 bytes
- session scaling: 177,427 bytes
- FTS/search: 3,578,269 bytes
- memory/summary: 51,227 bytes
- TUI product scenario: 9,769,356 bytes
- coding tasks: 90,743 bytes
- stress: 130,142 bytes

These are Python allocation observations, not a hard operating-system RSS cap.
Context, result counts, history materialization, activity display, memory entry
count/size, and concurrency all have explicit bounds.

## 16. Real coding benchmark results

Eight deterministic tasks ran in disposable real workspaces:

1. simple Python bug fix
2. multi-file Python bug fix
3. add and execute tests
4. read-only code review
5. create a small project
6. compile and test C++
7. repository exploration
8. three-Subagent investigation

All 8 completed with `FINAL_RESPONSE`, and independent task-specific
verification passed. The result therefore distinguishes protocol termination
from semantic evidence instead of treating final text alone as proof.

## 17. Live DeepSeek result

No Live DeepSeek call was run in this wave. The readiness check found neither a
visible `CODING_AGENT_LIVE_SECRET_FILE` pointer nor
`%TEMP%\coding-agent-deepseek-live.key`. Per the approved safety rule, the
offline suite continued and no credential was requested, synthesized, logged,
or persisted. The project had prior live verification, but this report makes no
new live-model quality claim.

## 18. Full regression and verification

Fresh final evidence:

- Full suite: `python -m pytest -q` -> `814 passed, 11 skipped in 104.56s`.
- Product/Agent/Context/Session/Skill/Plugin/Subagent/benchmark E2E group:
  `28 passed in 36.03s`.
- Repair-affected Provider/Tool/Application group: `81 passed in 3.92s`.
- Security/isolation-focused group: `200 passed, 9 skipped in 11.35s`.
- Doctor/live-harness-offline/TUI product smoke: `10 passed in 9.77s`.
- Compile: `python -m compileall -q src tests scripts benchmarks` -> exit 0.
- CLI help: `python -m coding_agent --help` -> exit 0.
- Benchmark CLI help: `python -m benchmarks.run --help` -> exit 0.
- Final standard benchmark: 11/11 scenarios passed.

Skipped tests are platform/capability alternatives (for example fallback paths)
and do not hide a failure.

## 19. Security and compliance

- A production/dependency scan found no LangChain, LangGraph, LlamaIndex,
  OpenAI Agents SDK, Claude Agent SDK, AutoGen, CrewAI, Semantic Kernel, Mem0,
  Supermemory, or other Agent Framework dependency/import.
- Secret-shaped value scan found no production hit. Its only two repository
  hits are intentional negative test fixtures for Workspace Memory secret
  rejection.
- Provider keys remain runtime-only and are filtered from child command
  environments and product output.
- Path traversal, outside-workspace symlinks, command environment filtering,
  session/recall workspace isolation, Skill non-executable guidance, and Plugin
  trust boundaries are covered by the passing security-focused suite.
- The prior temporary live credential does not exist; no cleanup action was
  needed in this wave.

## 20. Remaining limitations

- `execute_command` uses `subprocess`, `shell=True`, and a timeout; it is not an
  OS sandbox and does not guarantee termination of every descendant process.
- Cancellation is cooperative. A synchronous provider request or shell command
  may not stop immediately at every operating-system boundary.
- Session persistence has no cross-process writer lock. The supported product
  model is one Coding Agent process per workspace at a time.
- Session JSON is local plaintext and may contain task text, source excerpts,
  and tool output. Users must not paste secrets into tasks.
- FTS speedup and wall-clock timings depend heavily on filesystem/OS cache.
- Tiny cached read operations can be slower when scheduled in parallel.
- A synthetic immediate 40-event TUI burst took 3.31 s under Textual test pilot
  plus `tracemalloc`; activity is bounded and normal agent events are sparse,
  but this is not a high-frequency telemetry UI.
- FakeModelClient proves deterministic protocol behavior, not subjective live
  model summary/candidate quality.

None is a newly discovered P0/P1 blocker under the approved single-process,
local-workspace architecture.

## 21. Git checkpoint suggestion

Git was not modified by the agent. Suggested files for the owner-managed
checkpoint:

- `benchmarks/__init__.py`
- `benchmarks/common.py`
- `benchmarks/fakes.py`
- `benchmarks/scenarios.py`
- `benchmarks/coding_tasks.py`
- `benchmarks/stress.py`
- `benchmarks/run.py`
- `benchmark_results/baseline.json`
- `benchmark_results/final.json`
- `tests/test_benchmark_suite.py`
- `tests/test_final_hardening.py`
- `src/coding_agent/tools/registry.py`
- `src/coding_agent/providers/openai_compatible.py`
- `src/coding_agent/application/service.py`
- `docs/superpowers/plans/2026-08-30-final-hardening-benchmark-plan.md`
- `docs/final-hardening-report.md`

Suggested commit message:

```text
test: finalize architecture hardening and bounded benchmarks
```

## 22. Submission readiness

The repository is ready for owner review, Git commit, and a final two-minute
demo rehearsal. The strongest demo remains a real local task: inspect files,
run a failing test, make a minimal edit, rerun tests, and show the session/tool
activity in TUI. This report and the JSON benchmark artifacts support technical
questions without turning timing variance into unsupported claims.

## 23. Final acceptance gate

### Correctness

1. **Agent Loop stable:** Yes. 1/2/5/10-step and multi-tool scenarios passed;
   original Agent E2E remains green.
2. **ToolCall/ToolResult complete:** Yes. Pairing remains canonical, and
   duplicate provider IDs are now rejected before dispatch.
3. **Termination reliable:** Yes at protocol level. Final response, max steps,
   cancellation, provider failure, and error paths are tested. This is not a
   claim of semantic correctness by itself.
4. **Semantic verification evidence correct:** Yes. Coding tasks use external
   file/test/compile assertions; 8/8 verifiers passed.

### Reliability

5. **Provider failure recovery reasonable:** Yes. Bounded retry and explicit
   transport/protocol errors are tested; malformed IDs are rejected.
6. **Tool failure recovery reasonable:** Yes. Structured errors cover malformed
   arguments, unknown tools, non-zero commands, timeout, and unexpected plugin
   failures.
7. **Session corruption degrades safely:** Yes. Canonical load failures are
   isolated and recovery paths pass.
8. **Derived-index corruption recoverable:** Yes. SQLite/FTS indexes are
   rebuildable and literal fallback is tested.
9. **Cancellation consistent:** Yes within cooperative cancellation semantics;
   unfinished turns are not committed.
10. **Subagent failure isolated:** Yes. Child results contain failure and the
    parent remains the sole writer.

### Performance

11. **Context growth controlled as History grows:** Yes. Complete canonical
    history reached 649,913 characters while Context stayed below 4,800.
12. **Latest Session has a fast path:** Yes. The catalog stores the latest
    locator and avoids scanning every history.
13. **Search avoids full History scan:** Yes with FTS5. It loaded ten matching
    files rather than 999; deterministic scan remains only a fallback.
14. **Parallel Tool has actual speedup:** Yes for controlled I/O wait (2.620x),
    with the explicit limitation that tiny cached reads are slower.
15. **Subagents have actual parallel speedup:** Yes, 2.699x for three independent
    investigations.
16. **Obvious duplicate model calls:** No. A typical simple TUI turn used one
    parent call and no unnecessary control-plane/child call.
17. **Obvious unnecessary large-object copies:** No in audited hot paths.
    Child contexts are compact and indexes materialize bounded hits; canonical
    history is retained deliberately.

### Resources

18. **Memory bounded:** Yes at the application-data/concurrency layers; all
    representative allocation scenarios stayed bounded. This is not an OS RSS
    hard limit.
19. **Worker/client/DB correctly closed:** Yes for audited ownership paths. The
    plugin-close/client-close ordering defect was repaired; injected clients
    retain caller ownership.
20. **TUI responsive:** Yes for normal product flows: work stays off the UI
    event path, switch/resize/cancel tests pass, and activity is bounded. The
    synthetic burst limitation is recorded above.

### Security

21. **Credential leak is zero:** Yes for production sources/artifacts scanned in
    this wave. Only deliberate secret-rejection test fixtures matched.
22. **Workspace boundary preserved:** Yes, including traversal and symlink
    regression coverage.
23. **Plugin/Skill trust boundary preserved:** Yes. Skills remain subordinate
    non-executable guidance; plugin tools remain registry-controlled local code,
    not a sandbox.

### Product

24. **TUI can complete a full Coding Task:** Yes. Product E2E repairs through
    tools and three Subagents, persists, resumes, handles failure, and cancels.
25. **Session/Skill/Plugin/Memory manageable:** Yes through the existing CLI/TUI
    product services and tested persistence paths.

### Compliance

26. **No Agent Framework:** Yes. Core history, Context, memory, tools, parsing,
    loop, termination, error handling, and Subagent orchestration are local.
27. **Core design remains explainable:** Yes. Ownership is modular and explicit;
    benchmark code exercises public runtime APIs instead of introducing an
    alternative architecture.

## Final verdict

There is no known P0/P1 blocker, the three evidence-backed hardening defects are
fixed, the standard benchmark and full regression pass, and the limitations are
compatible with the approved scope.

**CODING AGENT v1 FINAL / ARCHITECTURE FROZEN**

# Subagent System v1 FINAL Design

## Status and scope

This specification records the approved Subagent direction: one Python process,
real parallel read-only children in a `ThreadPoolExecutor`, and one parent writer.
It adds orchestration around the existing `AgentRunner`; it does not add another
Agent Loop or redesign Context, Memory, Session, Skill, Plugin, Provider, or
termination behavior.

Writable children, worktrees, multiprocessing, remote workers, nested children,
child sessions, child memory writes, child Plugin tools, planner frameworks, and
all Agent/Multi-Agent frameworks remain out of scope.

## Runtime architecture

The parent registry gains one source-owned control tool, `delegate_tasks`. A
synchronous dispatch submits one to three independent child tasks together,
waits for all futures, returns bounded results in input order, and only then lets
the parent Agent Loop continue. Parent file mutation and command verification
therefore cannot overlap child investigation.

Every child owns a new model client, read-only registry, `ContextManager`,
`ConversationHistory`, and existing `AgentRunner`. The only child tools are
`list_files`, `search_text`, and `read_file`. There is no `write_file`,
`replace_in_file`, `execute_command`, `delegate_tasks`, memory mutation, session
mutation, or Plugin tool. The shared workspace and configuration are immutable
or read-only from every child.

## Models, roles, and result protocol

`SubagentTask` contains stable `id`, bounded `task`, `role`, and `context_mode`.
Roles are exactly `explore`, `analysis`, and `review`; modes are exactly `fresh`
and `fork`. Roles select one small child instruction profile and do not create a
role framework.

`SubagentResult` contains `task_id`, `role`, existing `RunStatus`, bounded
`result`, `steps`, and bounded sanitized `error`. Child failure is data: a batch
may return FINAL_RESPONSE, MODEL_ERROR, and MAX_STEPS results together. Only an
unrecoverable manager/control failure makes the parent ToolResult fail.

The control ToolResult is strict JSON and is the only child-derived data added
to parent canonical history. Child messages, ToolCalls, ToolResults, reasoning,
and histories remain ephemeral and are never written to parent sessions.

## Context isolation

Fresh mode uses the Core Subagent Prompt, the delegated task, relevant current
Workspace Memory, and the parent's already-active Skills. It receives no parent
conversation.

Fork mode additionally receives the most recently built, bounded parent Context
View as an immutable, deterministically rendered snapshot. The snapshot is
clearly marked untrusted subordinate context and cannot override the child core
prompt or read-only registry. It is truncated before entering the child task;
the child still obeys its independent ContextPolicy. Parent and child histories
share no mutable backing objects after creation.

AgentRunner receives two generic optional callbacks: one at run start to reset
run-scoped delegation budgets, and one after each context build to publish the
immutable model-facing snapshot. Existing callers omit both and retain existing
behavior.

## Limits and deterministic aggregation

`SubagentLimits` defaults are:

- `max_parallel_subagents = 3`
- `max_subagent_tasks_per_batch = 3`
- `max_subagents_per_parent_run = 6`
- `max_subagent_steps = 8`
- `max_delegation_depth = 1`
- `max_subagent_result_chars = 6000`
- `max_total_subagent_result_chars = 16000`
- `max_fork_context_chars = 12000`

Tasks receive monotonically assigned IDs within one parent run. Results always
return in submitted order even when completion order differs. Per-result and
total result budgets use deterministic head/tail truncation. A fingerprint of
role plus whitespace-normalized, case-folded task rejects exact repeat
delegation within the same parent run. Batch/run exhaustion returns
`SUBAGENT_LIMIT_REACHED`; exact repeats return `SUBAGENT_DUPLICATE`.

## Parallelism, lifecycle, and observability

The manager creates a bounded `ThreadPoolExecutor` for each synchronous batch.
Every worker creates and closes its own ModelClient in `finally`. The parent
client is never shared or closed by children. A failed child never cancels its
siblings.

`SubagentEvent` reports batch start, stable task start announcements, individual
completion status, and collection completion. The normal CLI prints only these
concise events, not child tool traces or full output. Concurrency tests use
Barrier/Event probes rather than elapsed-time assumptions.

## Existing subsystem integration

One-shot, interactive, and background parent runtimes build their normal six
tools and enabled Plugins, then add `delegate_tasks`. Each runtime receives its
own manager. Relevant Workspace Memory and active Skills are copied as immutable
inputs to newly built child ContextManagers. Child selection, history, and
results never write Memory, Skill state, Session indexes, or Plugin state.

Parent system guidance says to delegate only genuinely independent exploration,
analysis, review, and call-site work; the parent remains responsible for design,
editing, command execution, tests, semantic verification, and final response.

## Failure and security behavior

All validation is local and strict. Invalid task shapes are MALFORMED_ARGUMENTS;
budget and duplicate failures use stable control error codes. Model, max-step,
stall, and child internal outcomes remain individual SubagentResults. Result and
error text redact configured sensitive values before deterministic truncation.
No API key, mutable model script, registry, ContextManager, history, or session
record is shared between child threads.

## Testing and freeze gate

Offline tests cover models/profiles, the exact read-only registry, fresh/fork
isolation, independent client lifecycle, true execution overlap, ordering,
limits, duplicate protection, truncation, failures, parent ToolCall persistence,
CLI events, one-shot/interactive/background composition, and the parser repair
E2E. Full pre-Subagent regressions, framework/credential scans, compile checks,
and the sixteen requested architecture questions must pass before declaring
`Subagent System v1 FINAL`. Live DeepSeek concurrency is a final optional model
behavior check and never blocks offline development.

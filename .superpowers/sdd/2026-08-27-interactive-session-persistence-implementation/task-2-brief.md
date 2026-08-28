### Task 2: User-Led Deterministic Context Selection

**Files:**
- Modify `src/coding_agent/context.py`
- Modify `tests/test_context.py`

**Interfaces:**
- Consume Task 1 `ConversationHistory.messages`, containing one system message followed by one or more user-led turns.
- Preserve `ContextManager.build(history) -> tuple[Message, ...]`.
- Produce context that always retains system, first user, and the latest user-led turn, or raises `ContextBudgetError`.

#### Step 1: Add failing grouping and budget tests

Construct histories with first-task assistant/tool tails and at least two later user turns. Prove:

- a later user starts a new group;
- tool-call/result batches remain together;
- old groups evict before newer groups;
- the latest user request and its existing tail are never silently removed;
- anchors plus an oversized latest turn raise `ContextBudgetError` rather than returning anchors alone;
- `recent_turns=1` retains the required latest group in addition to anchors.

The approved ruling is that messages after the permanent first-user anchor and before the next user form the selectable first-turn tail group. Every later user starts a new group.

#### Step 2: Verify RED

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-red" tests/test_context.py
```

Record failures caused by the current assistant-led grouping or current removal of a required latest turn.

#### Step 3: Minimal implementation

Replace assistant-led grouping with deterministic user-led grouping over `messages[2:]`: start a group for a user message or when no group exists; append every assistant/tool message to the current group. Select at most the most recent `recent_turns` groups. The last selected group is required. Validate anchors plus that group before evicting any older selected groups. Raise a concise `ContextBudgetError` if required content alone exceeds budget. Do not mutate canonical history and do not split a group.

Do not add summary, action journal, tokenizer dependency, long-term memory, or persistence behavior.

#### Step 4: Verify GREEN and regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-target" tests/test_context.py tests/test_agent.py tests/test_end_to_end.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-full"
```

All targeted and full tests must pass.

#### Step 5: Self-review and report

Review original anchors, latest-turn guarantee, recent-turn counting, tool-batch integrity, oversize error, deterministic ordering, and excluded scope.

Git operations are forbidden. Write the full report with RED/GREEN evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-2-report.md`.

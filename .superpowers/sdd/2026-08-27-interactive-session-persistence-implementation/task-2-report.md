# Task 2 Report: User-Led Deterministic Context Selection

## Scope delivered

- Replaced assistant-led context grouping with user-led grouping in
  `src/coding_agent/context.py`.
- Retained the permanent system and original-user anchors.
- Made the newest selected group mandatory: anchors plus that group are
  validated before any older group is evicted.
- Added tests in `tests/test_context.py` for later-user grouping, tool-batch
  integrity, deterministic oldest-first eviction, the `recent_turns=1`
  guarantee, and oversized latest-group errors.

## TDD evidence

Tests were added before the production change. The production change that the
new tests were designed to catch was assistant-led grouping, which could
separate a later user request from its tail and silently discard the required
latest request under budget pressure.

### RED

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-red" tests/test_context.py
```

Result: `5 failed, 16 passed in 0.13s`.

Expected failures:

- `test_recent_turns_one_keeps_latest_user_led_group_beside_anchors` omitted
  `latest request` and retained only the assistant response.
- `test_later_user_turn_keeps_tool_call_and_result_batch_together` omitted
  the later user request from the tool batch's selected context.
- `test_budget_removes_oldest_user_led_group_before_newer_groups` omitted the
  newest request and retained only its assistant response.
- `test_later_users_start_distinct_groups_in_deterministic_order` separated
  later user requests from their assistant tails.
- `test_build_rejects_oversized_latest_user_group_instead_of_dropping_it` did
  not raise; the previous code silently dropped required latest content.

### GREEN / regression

Targeted command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-target" tests/test_context.py tests/test_agent.py tests/test_end_to_end.py
```

Result: `41 passed in 0.36s`.

Full command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task2-full"
```

Result: `198 passed, 5 skipped in 7.06s`.

## Self-review

- Original anchors remain `messages[:2]`; the anchor-over-budget error is
  unchanged.
- `_group_turns(messages[2:])` opens a group for every user message, or for
  the initial first-turn tail when no group exists. Assistants and tools append
  to that current group, so batches are never split.
- The most recent `recent_turns` groups are selected deterministically in
  history order. Budget eviction removes only the oldest selected complete
  group.
- Before eviction, anchors plus the final selected group are checked. If they
  exceed the budget, `ContextBudgetError` is raised rather than returning
  anchors alone or dropping the latest request/tail.
- `build` reads the immutable history snapshot and does not mutate canonical
  history.
- No persistence, summaries, action journal, tokenizers, long-term memory, or
  network/API behavior was added.

## Repository handling

No Git operations were performed and no commit was created (owner-managed).

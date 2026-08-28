### Task 1: Copyable Conversation History and One-Turn AgentRunner

**Files:**
- Modify: `src/coding_agent/context.py`
- Modify: `src/coding_agent/agent.py`
- Modify: `src/coding_agent/system_prompt.py`
- Modify: `tests/test_context.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produce `ConversationHistory(system_prompt: str, original_user_task: str | None = None)`.
- Produce `ConversationHistory.from_persisted(system_prompt: str, messages: tuple[Message, ...]) -> ConversationHistory`.
- Produce `ConversationHistory.copy() -> ConversationHistory`.
- Produce `ConversationHistory.persisted_messages -> tuple[Message, ...]`, excluding the system message.
- Produce `AgentRunner.run_turn(history: ConversationHistory, user_message: str) -> RunResult`.
- Preserve `AgentRunner.run(system_prompt: str, original_user_task: str) -> RunResult`.

#### Step 1: Add failing history lifecycle tests

Prove an empty history contains only the system message, persisted recovery injects the supplied current system prompt, system roles in persisted messages are rejected, the first persisted message must be user, and copies do not share mutation.

#### Step 2: Add failing AgentRunner multi-turn tests

Use one FakeModelClient script for two `run_turn` calls and assert both user messages and both final assistant messages remain canonical. Add a separate `max_steps=1` test proving each `run_turn` receives a fresh one-step budget. Preserve existing one-shot tests.

#### Step 3: Verify RED

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-red" tests/test_context.py tests/test_agent.py tests/test_config.py
```

Record the relevant expected failures before implementation.

#### Step 4: Minimal implementation

The optional original task is appended only when non-`None`. Recovery rejects an empty persisted tuple or any tuple whose first message is not user, and rejects every persisted system role. Copies have independent mutable backing lists. `persisted_messages` returns the immutable non-system snapshot.

Move the existing bounded loop body to `run_turn`, append the supplied user message before step 1, and append a final assistant `Message` before returning `FINAL_RESPONSE`. Keep step and repeated-failure fingerprint state local to each call. Make `run()` create a system-only history and delegate once.

Add this exact system-prompt policy without CLI translation:

```text
Unless the user explicitly requests another language, respond in the language of the latest user message.
```

#### Step 5: Verify GREEN and regression

Run:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-target" tests/test_context.py tests/test_agent.py tests/test_config.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task1-full"
```

All targeted and full tests must pass with pristine output.

#### Step 6: Self-review and report

Review mutation isolation, persisted-message validation, one-shot compatibility, final-message retention, per-turn step/failure reset, response-language rule, and scope. Do not implement persistence or context regrouping in this task.

Git operations are forbidden. Write the complete task report to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-1-report.md`, including RED/GREEN commands and exact results, files changed, self-review findings, and concerns.

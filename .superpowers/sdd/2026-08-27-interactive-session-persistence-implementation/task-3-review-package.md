# Task 3 review package (owner-managed Git exception)

Review current contents of exactly these files; Git operations are prohibited:

- `src/coding_agent/session.py`
- `tests/test_session.py`

Requirements: `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-3-brief.md`

Evidence: `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-3-report.md`

The existing `src/coding_agent/protocol.py` is the consumed protocol contract and may be read only for a named compatibility risk. Scope is codec, record, validation, and known-value redaction; disk storage is Task 4. Review is read-only and must not run Git.

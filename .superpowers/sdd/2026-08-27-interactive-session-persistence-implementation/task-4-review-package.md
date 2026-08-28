# Task 4 review package (owner-managed Git exception)

Review current contents of exactly these files; Git operations are prohibited:

- `src/coding_agent/session_store.py`
- `tests/test_session_store.py`

Requirements: `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-brief.md`

Evidence: `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-report.md`

`src/coding_agent/session.py` is the accepted consumed codec and may be inspected only for a named boundary risk. Scope is storage home, IDs, workspace index/isolation, strict loads, atomic write order, and errors. Review is read-only and must not run Git.

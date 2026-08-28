# Task 4 Report: Atomic JsonSessionStore and Workspace Index

## Status

Implemented Task 4 in the owner-managed working tree. No Git operations were performed.

## TDD evidence

### RED

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-red" tests/test_session_store.py
```

Exact output (exit code 1):

```text
=================================== ERRORS ====================================
________________ ERROR collecting tests/test_session_store.py _________________
ImportError while importing test module 'D:\proj\tests\test_session_store.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
C:\Python313\Lib\importlib\__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_session_store.py:13: in <module>
    from coding_agent.session_store import (
E   ModuleNotFoundError: No module named 'coding_agent.session_store'
=========================== short test summary info ===========================
ERROR tests/test_session_store.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.38s
```

The failure was the expected missing production module, not a test syntax or fixture error.

### First GREEN attempt and correction

The first targeted run reached 66 passing tests and one failing test. The failure was confined to the test's Windows interpretation of the injected POSIX-looking XDG path. The following block is a relevant abbreviated excerpt, not exact output:

```text
.............................................F.....................      [100%]
================================== FAILURES ===================================
_ test_resolve_session_home_uses_platform_fallbacks[linux-environ2-expected_tail2] _
[prose omission: intervening traceback details from this earlier failure are not reproduced]
E       AssertionError: assert WindowsPath('/isolated/xdg/coding-agent') == WindowsPath('C:/isolated/xdg/coding-agent')
=========================== short test summary info ===========================
FAILED tests/test_session_store.py::test_resolve_session_home_uses_platform_fallbacks[linux-environ2-expected_tail2]
1 failed, 66 passed in 0.82s
```

The expected value was corrected to use the injected `XDG_DATA_HOME` literal directly rather than `Path.is_absolute()`, whose semantics follow the host Windows path flavor. Production behavior was unchanged.

### Final targeted GREEN

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-target" tests/test_session.py tests/test_session_store.py
```

Exact output (exit code 0):

```text
....................................................................     [100%]
68 passed in 0.43s
```

### Full regression GREEN

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-full"
```

Exact output (exit code 0):

```text
........................................................................ [ 26%]
...............................s...............ss....................... [ 53%]
........................................................................ [ 79%]
.....................ss................................                  [100%]
266 passed, 5 skipped in 7.57s
```

## Files changed

- Created `src/coding_agent/session_store.py`.
- Created `tests/test_session_store.py`.
- Created this report at `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-report.md`.
- `src/coding_agent/session.py` was not modified; no codec boundary defect was exposed.

## Implemented behavior

- Platform-specific storage-root resolution with `CODING_AGENT_HOME` taking precedence.
- Twelve-character lowercase-hex IDs using `secrets.token_hex(6)`, injected-ID validation, and collision retry without creating storage artifacts.
- Strict existing-directory workspace canonicalization, Windows `normcase` identity, full SHA-256 workspace index names, and independent stored-workspace checks.
- In-memory-only empty session creation using one injected clock value for both initial timestamps.
- Strict session and workspace-index loading with the approved stable error codes and concise messages.
- Exact v1 workspace-index validation and deterministic latest/session-ID maintenance.
- Session-first, index-second atomic persistence with unique same-directory temporary files, UTF-8, flush, `fsync`, best-effort POSIX mode `0600`, `os.replace`, and exact-temp cleanup.
- Save returns the persisted immutable record, preserving `created_at` and caller provider/model/messages while updating `updated_at`.

## Self-review

- **Write order:** session replacement occurs before index replacement and is directly asserted through the narrow atomic-write boundary.
- **Exact-temp cleanup:** replacement failure removes only the unique temporary path created by that call; an unrelated temp file remains untouched.
- **Index preservation:** session replacement failure preserves the old session bytes; later index replacement failure leaves the old index byte-for-byte intact while the new session remains readable.
- **No empty write:** `create_session` performs no filesystem write, and the strict Task 3 serializer continues to reject empty histories before `save` reaches either atomic-write call.
- **Canonical isolation:** workspaces resolve strictly, index names hash normalized canonical identity, and both index and session documents are checked independently against the requested workspace.
- **Path portability:** home fallback behavior is tested by patching module platform and home lookup; hashing applies case normalization only on actual Windows.
- **Stable errors:** invalid/unknown IDs, corrupt or unsupported sessions, corrupt indexes, workspace mismatch, read errors, and save errors retain approved codes without raw host exception text.
- **Excluded scope:** no locking, database, encryption, API-key handling, cleanup/list/delete API, interactive UI, CLI change, or cross-file transaction mechanism was added.

## Concerns

None. The five full-suite skips are pre-existing environment-dependent skips and are not Task 4 failures.

---

## Fix round 1: decode classification and bounded ID generation

### Findings and root causes

1. `Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` for invalid bytes. That exception is not an `OSError`, so it bypassed both storage read error translators before any text reached the Task 3 codec.
2. `create_session` used an unbounded `while True`, and called the injected ID generator outside exception translation. Permanent collisions therefore never terminated, while iterator exhaustion and generator failures escaped as raw host exceptions.

### Focused RED

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-fix1-red" tests/test_session_store.py -k "one_hundred or id_generator_failure or invalid_utf8"
```

Exact output summary and failure evidence (exit code 1):

```text
FFFFF                                                                    [100%]
================================== FAILURES ===================================
__________ test_create_session_stops_after_one_hundred_id_collisions __________
tests\test_session_store.py:160: in test_create_session_stops_after_one_hundred_id_collisions
    assert_error(
tests\test_session_store.py:36: in assert_error
    operation()
tests\test_session_store.py:161: in <lambda>
    "SESSION_SAVE_FAILED", lambda: store.create_session(workspace, "provider", "model")
src\coding_agent\session_store.py:79: in create_session
    session_id = self._id_generator()
tests\test_session_store.py:155: in colliding_id
    raise AssertionError("session id collision retries exceeded the limit")
E   AssertionError: session id collision retries exceeded the limit
________ test_create_session_translates_id_generator_failure[failure0] ________
tests\test_session_store.py:175: in fail_id
    raise failure
E   StopIteration
________ test_create_session_translates_id_generator_failure[failure1] ________
tests\test_session_store.py:175: in fail_id
    raise failure
E   RuntimeError: host-detail-secret
_____________ test_invalid_utf8_session_is_concise_corrupt_error ______________
src\coding_agent\session_store.py:109: in load_session
    text = self._session_path(session_id).read_text(encoding="utf-8")
<frozen codecs>:325: in decode
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
___________ test_invalid_utf8_index_is_concise_index_corrupt_error ____________
src\coding_agent\session_store.py:162: in _read_index
    text = self._index_path(workspace).read_text(encoding="utf-8")
<frozen codecs>:325: in decode
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
=========================== short test summary info ===========================
FAILED tests/test_session_store.py::test_create_session_stops_after_one_hundred_id_collisions
FAILED tests/test_session_store.py::test_create_session_translates_id_generator_failure[failure0]
FAILED tests/test_session_store.py::test_create_session_translates_id_generator_failure[failure1]
FAILED tests/test_session_store.py::test_invalid_utf8_session_is_concise_corrupt_error
FAILED tests/test_session_store.py::test_invalid_utf8_index_is_concise_index_corrupt_error
5 failed, 25 deselected in 0.46s
```

The collision double deliberately raised on an illegal 101st request, keeping RED bounded while proving the implementation exceeded the required 100 attempts.

### Minimal fix

- Added private `_SESSION_ID_ATTEMPTS = 100` and replaced the unbounded loop with exactly that many attempts.
- Translated injected generator exhaustion/failure (ordinary `Exception`, not process-control `BaseException`) to concise `SESSION_SAVE_FAILED`.
- Classified session UTF-8 decode failure as concise `SESSION_CORRUPT` and index UTF-8 decode failure as concise `SESSION_INDEX_CORRUPT`.
- Preserved the existing `FileNotFoundError`, ordinary `OSError`, Task 3 codec, malformed-ID, and successful collision-retry paths.

### Focused GREEN

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-fix1-focused" tests/test_session_store.py -k "one_hundred or id_generator_failure or invalid_utf8"
```

Exact output (exit code 0):

```text
.....                                                                    [100%]
5 passed, 25 deselected in 0.10s
```

### Targeted regression GREEN

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-fix1-target" tests/test_session.py tests/test_session_store.py
```

Exact output (exit code 0):

```text
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 0.74s
```

### Full regression GREEN

Command:

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-fix1-full"
```

Exact output (exit code 0):

```text
........................................................................ [ 26%]
...............................s...............ss....................... [ 52%]
........................................................................ [ 78%]
..........................ss................................             [100%]
271 passed, 5 skipped in 8.07s
```

### Fix-round files changed

- Modified `src/coding_agent/session_store.py`.
- Modified `tests/test_session_store.py`.
- Appended this evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-report.md`.

### Fix-round self-review

- Invalid session bytes are classified separately from filesystem failures; valid UTF-8 still flows through `deserialize_session`, preserving `SESSION_CORRUPT` and `SESSION_VERSION_UNSUPPORTED` from Task 3.
- Invalid index bytes are classified as index corruption; malformed JSON and strict index schema validation remain unchanged.
- Missing session files still produce `SESSION_NOT_FOUND`; ordinary session/index read errors still produce concise `SESSION_IO_ERROR`.
- A permanent valid-ID collision performs exactly 100 attempts, then produces concise `SESSION_SAVE_FAILED` without changing the colliding file.
- Empty iterator and ordinary injected generator failures produce concise `SESSION_SAVE_FAILED` without leaking exception details.
- Malformed generated IDs still fail immediately, while one collision followed by a valid free ID still succeeds.
- No Task 5 interactive, Task 6 CLI, locking, cleanup, or other scope was added.

### Fix-round concerns

None. The five full-suite skips remain pre-existing environment-dependent skips.

---

## Fix round 2: report clarification only

The abbreviated block under **First GREEN attempt and correction** is an excerpt from the original Task 4 first-GREEN portability failure. Its omission marker does not belong to Fix round 1 RED evidence and does not claim to reproduce that earlier traceback exactly.

The **Fix round 1: decode classification and bounded ID generation** focused RED block is complete as recorded: it starts with all five failures, identifies the collision-limit, generator-exhaustion/failure, session-decode, and index-decode failures, and ends with the exact `5 failed, 25 deselected in 0.46s` summary. No missing historical output was invented.

### Fix round 2 files changed

- Modified only `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-report.md`.
- Production code and tests were not altered.

### Fix round 2 self-review

- The earlier first-GREEN block is explicitly labeled as an abbreviated excerpt rather than exact output.
- The ambiguous raw ellipsis was replaced with an explicit prose omission marker tied to that earlier traceback.
- Fix round 1 RED evidence remains unchanged and is clearly distinguished from the earlier first-GREEN excerpt.
- No test rerun was performed because this correction changes documentation only; the recorded 73 targeted and 271 full GREEN results remain the latest code evidence.

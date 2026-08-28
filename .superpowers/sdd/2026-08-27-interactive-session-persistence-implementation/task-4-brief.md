### Task 4: Atomic JsonSessionStore and Workspace Index

**Files:**
- Create `src/coding_agent/session_store.py`
- Create `tests/test_session_store.py`
- Modify `src/coding_agent/session.py` only if a store test exposes a codec boundary defect

**Public interfaces:**
- `resolve_session_home(environ: Mapping[str, str] | None = None) -> Path`
- `utc_now() -> datetime`
- `generate_session_id() -> str` using `secrets.token_hex(6)`
- `JsonSessionStore(root: Path, clock: Callable[[], datetime] = utc_now, id_generator: Callable[[], str] = generate_session_id)`
- `create_session(workspace: Path, provider: str, model: str) -> SessionRecord` with no immediate disk write
- `load_latest(workspace: Path) -> SessionRecord | None`
- `load_session(session_id: str, workspace: Path) -> SessionRecord`
- `save(record: SessionRecord) -> SessionRecord`, returning the persisted record with updated UTC metadata

#### Step 1: Add failing home, ID, and containment tests

Cover:

- `CODING_AGENT_HOME` wins and denotes the storage root itself;
- Windows fallback `%LOCALAPPDATA%\coding-agent`;
- macOS fallback `~/Library/Application Support/coding-agent`;
- Linux `$XDG_DATA_HOME/coding-agent`, else `~/.local/share/coding-agent`;
- tests patch the module platform/environment/home resolution and never touch real user storage;
- workspace must be an existing directory and is stored as `resolve()`; its index filename is SHA-256 of the canonical UTF-8 path string, with Windows `normcase` before hashing;
- generated IDs are exactly 12 lowercase hex; an existing colliding session filename causes retry with the next injected ID;
- `create_session` returns empty-message in-memory metadata with one clock value for both timestamps and writes no session/index/root artifact;
- explicit load verifies the stored resolved workspace equals the requested resolved workspace.

#### Step 2: Add failing multiple-session/recovery/index tests

For one workspace save two sessions; for another save one. Assert:

- layout is `<root>/sessions/<id>.json` and `<root>/workspaces/<full_sha256>.json`;
- index schema exact fields are `schema_version`, `workspace`, `latest_session_id`, `session_ids`;
- `session_ids` preserves every session without duplicates; saving an existing old session makes it latest and moves it to the end deterministically;
- `load_latest` returns `None` only when the workspace index does not exist;
- latest load, explicit older load, and persisted provider/model/messages/timestamps are correct;
- invalid/unknown ID → `SESSION_NOT_FOUND`;
- different requested workspace → `SESSION_WORKSPACE_MISMATCH`;
- malformed/unknown-field/wrong-workspace index → `SESSION_INDEX_CORRUPT`;
- malformed session retains Task 3 `SESSION_CORRUPT`; unsupported version retains `SESSION_VERSION_UNSUPPORTED`;
- ordinary read/path permission or filesystem errors map to concise `SESSION_IO_ERROR` without host exception detail.

Index validation requires schema version 1, absolute canonical workspace string, valid latest/session IDs, distinct session IDs, and latest contained in `session_ids`. A missing indexed latest session is an explicit error, never silent new-session creation.

#### Step 3: Add failing atomic-write tests

Patch only the narrow module `_atomic_write_text(path: Path, text: str) -> None` boundary or its `os.replace` call. Prove:

- session replacement failure returns `SESSION_SAVE_FAILED`, leaves the prior target bytes unchanged, and cleans only the exact unique temp file;
- a successful session replacement followed by index replacement failure returns `SESSION_SAVE_FAILED`; the new session JSON is readable while the prior index stays byte-for-byte unchanged;
- save order is session then index;
- no claim/test asserts a cross-file transaction.

#### Step 4: Verify RED

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-red" tests/test_session_store.py
```

Expected initial failure: module absent. Record it.

#### Step 5: Minimal implementation

Use standard-library path/hash/json/os/secrets/sys/tempfile/time functionality only.

- Resolve the existing workspace directory strictly before hashing or comparing. On actual Windows apply `os.path.normcase` to its resolved string for the hash/comparison identity.
- Store full canonical workspace in both session and index, and independently compare it on load to protect against hash collision/wrong workspace.
- `create_session` retries colliding filenames and rejects malformed injected IDs; it does not create directories/files.
- Preserve `created_at`; on `save`, set `updated_at` from the injected aware-UTC clock and persist caller-supplied provider/model/messages.
- Read an existing valid index before writing; move the saved ID to the end and set latest.
- Serialize and atomically replace the session first, index second.
- `_atomic_write_text` creates parent dirs, writes UTF-8 to a unique same-directory temp file, flushes, `os.fsync`s, applies best-effort POSIX `chmod(0o600)`, then `os.replace`s. On failure it removes only its exact temp path and leaves target intact.
- Translate errors only at public storage boundaries into the approved stable codes: `SESSION_NOT_FOUND`, `SESSION_CORRUPT`, `SESSION_VERSION_UNSUPPORTED`, `SESSION_WORKSPACE_MISMATCH`, `SESSION_INDEX_CORRUPT`, `SESSION_IO_ERROR`, `SESSION_SAVE_FAILED`. Never include document contents, API keys, or raw OS exception text.

Do not add locking, database, encryption, session UI, API-key knowledge, cleanup/list/delete APIs, or cross-file transaction machinery.

#### Step 6: Verify GREEN and regression

```powershell
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-target" tests/test_session.py tests/test_session_store.py
python -m pytest -q --basetemp "$env:TEMP\coding-agent-task4-full"
```

#### Step 7: Self-review/report

Review write order, exact-temp cleanup, index preservation, no empty write, canonical workspace isolation, path portability, stable errors, and absence of excluded scope.

No Git operations. Write full RED/GREEN evidence to `.superpowers/sdd/2026-08-27-interactive-session-persistence-implementation/task-4-report.md`.

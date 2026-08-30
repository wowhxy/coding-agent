# Session Management v2 Final Design

## Goal and invariants

Canonical Session JSON remains the only complete source of truth for metadata,
summary state, and the full User/Assistant/ToolCall/ToolResult protocol. All new
state is disposable, workspace-scoped, and rebuildable. Existing session
semantics and public product behavior remain unchanged.

## Storage layout

For workspace digest `<id>` under the existing session home:

```text
sessions/<session-id>.json                 # canonical history
workspaces/<id>.json                       # legacy migration input only
workspaces/<id>/latest                     # atomic session-id fast path
workspaces/<id>/session_index.sqlite3      # derived catalog + search index
workspaces/<id>/stale                      # derived-state rebuild marker
```

The SQLite database contains:

- `catalog`: session id, name, created/updated timestamps, provider/model, and a
  bounded preview; no message history.
- `search_locators`: stable message ordinal/source metadata and session id.
- a contentless FTS5 table using `contentless_delete=1`; it contains indexed
  terms but no stored message body. Searchable projections are bounded before
  indexing and are never persisted as a second full transcript.
- schema/workspace metadata used to reject incompatible or cross-workspace DBs.

The installed SQLite 3.45.3 was verified to support FTS5 and
`contentless_delete=1`. If a target runtime lacks FTS5, search uses the bounded,
deterministic canonical-scan fallback and loads each session at most once.

## Hot paths

- Latest resume: read and validate `latest`, directly load exactly one canonical
  JSON, and verify workspace containment. Complexity is O(1) pointer lookup plus
  O(S) for the selected session.
- List: indexed catalog query ordered by `updated_at DESC, session_id ASC`, with
  `limit`/`offset`. No canonical history is opened.
- Search/Recall: query Top-K FTS locators with BM25 and deterministic recency/id/
  ordinal tie-breaks; group by session; load each matched canonical session once;
  materialize bounded snippets from the located message.
- Open/switch: load only the explicitly selected canonical session.

English, casefolded Unicode, Chinese, mixed text, paths, and code identifiers use
a deterministic normalized term representation. Title, user messages, visible
assistant responses, and bounded useful tool projections are indexed. Huge file
contents, raw source dumps, hidden reasoning, binary data, and credential-shaped
text are excluded or redacted.

## Persistence, migration, and recovery

Save order is canonical JSON first, then catalog/search, then latest pointer.
Derived failures leave canonical persistence successful and write a stale marker
where possible. Missing, stale, corrupt, or schema-incompatible derived state is
rebuilt from canonical JSON without rewriting it. Migration is lazy, idempotent,
restart-safe, and may consume the legacy index as a hint; a full O(T) scan is
permitted only for migration/recovery.

Rename and delete update canonical state first and then derived state. Deleting
the latest session selects the next catalog entry or clears the pointer. A
derived failure is repaired on the next catalog/search operation. Existing empty
`/new` sessions remain ephemeral until their first persisted turn, preserving
current product behavior; once persisted they become latest and indexed.

## TUI and observability

The sidebar requests a 50-entry catalog page. A non-empty filter uses the shared
search service; clearing it restores catalog results. Search results are
lightweight and switching alone loads their complete session. Existing new,
switch, rename, delete, keyboard, and context-menu behavior is preserved.

`SessionStoreReport` records `latest_fast_path_used`, `session_files_loaded`,
`full_history_files_loaded`, `catalog_entries_loaded`, `search_backend`,
`search_hits`, and `index_rebuilt`, without session content or credentials.

## Scope rulings

No tree/branch/worktree sessions, cloud/global search, folders, vector/embedding/
RAG system, external database/search service, or agent framework is introduced.
Cross-process locking remains outside this performance wave; SQLite protects its
own transactions while canonical JSON keeps the existing process-local locking
semantics.

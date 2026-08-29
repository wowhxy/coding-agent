# Context Management and Memory Finalization Design

Status: implementation-authorized by the repository owner. Baseline: 450 passed, 9 skipped; `compileall` exit 0.

## 1. Architecture and ownership

This wave incrementally freezes the existing architecture instead of replacing it:

`Canonical ConversationHistory -> deterministic Context View -> optional incremental Summary -> ModelClient`

The independent sources remain:

- `ConversationHistory`: complete session-owned protocol truth. Compression never edits it.
- `SummaryState`: session-owned, persistent derived coverage of older history.
- `WorkspaceMemoryStore`: workspace-owned, cross-session project knowledge.
- `RecallService`: temporary same-workspace retrieval from canonical session JSON through a rebuildable index.
- `ActiveSkill`: current-turn subordinate methodology guidance.
- `ContextManager`: deterministic selection, compression, budgeting, assembly, and reporting only.

Final request order is Core System Prompt, one subordinate Active Skills section, Original User Task, relevant Workspace Memory, Persistent Session Summary plus deterministic post-summary activity, Recalled History, then Recent Complete Turns. Current User Goal is not redesigned.

Alternatives rejected: rewriting history in place would destroy the source of truth; a vector database/RAG layer exceeds scope; persisting recall into memory would mix temporary evidence with confirmed knowledge. The selected design extends existing dataclasses and JSON stores with migrations and compatibility adapters.

## 2. Unified `ContextPolicy`

One immutable policy owns character budgets and thresholds:

- total context: existing configured `max_context_chars`;
- per Tool Result output: existing configured `max_tool_output_chars`;
- active Skills: 20,000 characters and three Skills, enforced by the Skill subsystem;
- Workspace Memory: 8,000 characters, Top-12;
- Summary: 8,000 characters;
- Recall: 6,000 characters, Top-8;
- preferred recent turns: existing configured `recent_turns`;
- minimum recent complete turns: two, or all available when fewer exist;
- summary trigger: 60,000 canonical-history characters.

`ContextManager` accepts a policy while preserving its current constructor arguments as a compatibility path. Character accounting remains deterministic JSON-serialized size; no tokenizer dependency is added.

## 3. Progressive Context View compression

All four levels operate on copied `Message` values and never alter canonical history.

1. **L1 Tool Result trimming:** parse valid ToolResult JSON in Context View and head/tail-truncate only `output`. The full result remains canonical.
2. **L2 stale result pruning:** for successful `read_file`, `search_text`, and `list_files`, identify calls by normalized tool name plus relevant explicit arguments. When a later successful call supersedes the same identity outside protected recent turns, replace only the earlier result payload with a bounded omission marker while retaining the Assistant ToolCall and matching Tool message. Failed results, recent evidence, `execute_command`, `write_file`, and `replace_in_file` are not stale-pruned.
3. **L3 deterministic activity compression:** old, unsummarized, non-protected tool interactions become a bounded `Earlier activity` list derived only from explicit tool names and arguments. It may say inspected/listed/searched/edited/wrote/ran, but cannot infer success or semantic conclusions not present in protocol data. This activity is rendered in the summary layer.
4. **L4 persistent LLM summary:** runs only when the summary threshold is reached or deterministic compression still leaves unsummarized older turns under pressure. It is the final compression mechanism, not the first.

If pressure remains, reduce Recall, then low-priority Memory, then older noncritical turns, while preserving anchors, the latest interaction, the minimum recent complete turns where physically possible, and complete ToolCall/ToolResult batches. An impossible mandatory set raises `ContextBudgetError`; the system never slices a final concatenated prompt.

## 4. Persistent incremental Summary

`SummaryState` adds `schema_version=1` while retaining `text`, `covered_message_count`, and `updated_at`. Coverage is the count of messages in the session tail after the System Prompt and Original User Task; it advances only over complete old turns outside the preferred recent window.

Session storage advances to schema v4. v1-v3 remain readable; a v3 summary is migrated to summary schema v1. Invalid schema, text, timestamp, or coverage is discarded as optional derived state while canonical messages remain recoverable. The next normal save writes v4.

An update request contains only the previous summary and newly old messages. It uses the existing ModelClient with no tools, is never appended to history, and is bounded to 8,000 characters. It preserves completed work, code changes, confirmed facts, validation results, failed approaches, unresolved issues, and continuation state. Provider/protocol/output/time failures preserve the previous valid summary and allow deterministic compression to continue.

## 5. Workspace Memory final model

Storage advances from schema v2 to v3. Each entry exposes `id`, `kind`, `key`, `content`, `source`, `created_at`, and `updated_at`. Existing `.text` remains a compatibility display property. v1/v2 records migrate in memory: explicit `key = content` text is split; otherwise a deterministic legacy key is derived without losing text. Normal writes use v3.

Kinds remain command, constraint, convention, architecture, and fact. Keys are normalized dotted identifiers; content is bounded text. Manual `/memory add` stays supported, accepting `key = content` and deriving a safe key for free text.

Quality rules are deterministic:

- exact and normalized key/content duplicates are ignored;
- equal normalized keys with different content are conflicts;
- confirmed replacement preserves ID and creation time;
- count/key/content limits are enforced;
- secret/credential patterns and current sensitive values are rejected or redacted according to the existing explicit/manual boundary.

Candidate extraction becomes strict `{key, content, kind, source}` JSON, maximum four candidates and at most one no-tools control-plane call after a normal interactive turn. Only explicit user constraints or real protocol/tool evidence is provided. Local validation rejects credentials, source dumps, large Tool Results, temporary hypotheses, and unsupported kinds. The model never writes memory; confirmation remains mandatory.

## 6. Relevant Memory retrieval

ContextManager receives structured memory projections. When memory is small, stable storage order is retained. Under count or character pressure, deterministic scoring uses token overlap against Original User Task plus latest user message, matching memory key/content/kind, with a retention bonus for constraints, then conventions/architecture/commands, then facts. Ties use stable storage order. Top-K and the memory character budget are both enforced, and included/dropped IDs are reported.

Memory remains strictly workspace-hashed and shared by `/new` sessions in the same workspace. New sessions never inherit another session's history or summary.

## 7. Session Recall

`RecallService` searches user messages, assistant messages, useful bounded Tool Results, and session metadata from the current workspace only. `RecallEntry` returns session ID, source/role, bounded excerpt, ordinal, session timestamp, and deterministic score.

The preferred index is Python stdlib SQLite FTS5 stored under the coding-agent home. It is derived and rebuildable: a fingerprint of workspace session IDs/update times detects staleness; missing or corrupt indexes are rebuilt from canonical JSON. Malformed individual sessions are skipped. If FTS5 is unavailable or a rebuild fails, deterministic literal/token scanning supplies the same result interface. No third-party search dependency is added.

`/recall <query>` displays results and stores a bounded pending result set for the next turn of that runtime session. Conservative automatic recall runs only for explicit past-reference signals such as 上次/之前/昨天/以前/previous/earlier/last time. Recall is temporary: after the turn it is cleared and never written to Workspace Memory, Summary, or ConversationHistory.

## 8. Context integration and observability

`ContextBuildReport` records only safe metadata: final serialized size, included Skill names, included/dropped memory IDs, whether Summary was used/updated, recalled session IDs/count, stale results pruned, Tool Results truncated, activity-compressed turns, and turns dropped. It never records bodies, source code, Tool payloads, credentials, memory content, or recall excerpts. `ContextManager.last_report` supports tests and optional debug use; no UI is added.

Skill selection, Summary generation, and Memory candidate extraction remain separate no-tools control-plane calls. Recall itself is local. None enters AgentRunner's tool loop or canonical history. Existing Skill authority and ToolRegistry boundaries remain unchanged.

## 9. Failure and recovery

- Invalid/corrupt Summary: discard only Summary, retain history, use L1-L3.
- Summary model failure: retain prior valid state and continue.
- Corrupt Memory: context uses empty memory; explicit management reports the stable error and never overwrites the corrupt file.
- Candidate failure/rejection/secret: skip without changing the agent result or persistence.
- Missing/corrupt Recall index: rebuild; if unavailable, scan canonical sessions.
- FTS unavailable: deterministic scan.
- Malformed session during recall: skip that session; other sessions remain searchable.
- Cross-workspace session/recall/memory access: reject or return no result.
- Oversized mandatory context: explicit `ContextBudgetError`, never destructive truncation.

## 10. Testing and freeze gate

All automated work is offline with FakeModelClient, temporary workspaces, real JSON persistence, real ContextManager/Memory/Session/Recall, and real ToolRegistry. The final E2E covers long-session L1-L4, restart and incremental summary, `/new` isolation with shared memory, explicit/automatic recall, cross-workspace isolation, and combined Skill/Memory/Summary/Recall/Tool Calling.

Regression covers AgentRunner, six tools, Provider abstraction, one-shot/interactive/background, session persistence, Skill System, termination, and demos. Final scans cover forbidden frameworks/dependencies and credentials. Live DeepSeek verification is deferred until all offline checks pass and is requested only for summary/candidate/continuation quality.

Architecture may be declared frozen only if the fourteen owner-specified freeze questions are answered from actual tests. No future compression/memory/summary/retrieval redesign is proposed absent a demonstrated defect.

## 11. Self-review

- **Coverage:** L1-L4, persistent incremental Summary, structured Memory, candidate control, relevance, Recall/index fallback, unified budget/report, integration, recovery, E2E, and freeze criteria are assigned.
- **Consistency:** canonical history remains authoritative; Summary is session-owned; Memory is workspace-owned; Recall is temporary; Skills remain current-turn guidance.
- **Backward compatibility:** Session v1-v3 and Memory v1-v2 migrate; existing constructor/CLI/tool/provider interfaces remain available.
- **Persistence safety:** only canonical session/memory JSON is authoritative; SQLite is disposable; optional derived corruption cannot destroy history.
- **Scope:** no Current Goal redesign, global memory, vector/embedding/RAG, external SDK, framework, new coding tool, Plugin, Multi-Agent, Web UI, or silent permanent learning.
- **Ambiguity rulings:** L3 lives in the summary layer; two recent turns are the minimum target; explicit Recall is pending for one next turn; automatic Recall is cue-gated; Memory storage schema becomes v3; Summary schema is v1 inside Session schema v4.
- **Placeholders:** no unfinished marker or undefined interface remains.

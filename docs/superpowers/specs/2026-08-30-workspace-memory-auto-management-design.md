# Workspace Memory Auto-Management — Final Design

## Scope and ruling

This bounded change modifies only how `WorkspaceMemoryStore` entries are formed
and maintained. Conversation History, Summary, Recall, Context assembly, Skills,
Plugins, Subagents, the Agent Loop, and the six coding tools remain unchanged.

The existing store remains the sole persistence layer and keeps atomic writes,
workspace isolation, schema compatibility, deduplication, key conflict handling,
and size/count/credential limits. A shared `MemoryAutoManager` is used by the
classic interactive shell and product/TUI service.

## Control flow

After a foreground turn reaches `FINAL_RESPONSE`, a local eligibility check may
permit one bounded, no-tools model extraction call. The response can propose at
most five candidates containing `key`, `content`, `kind`, `source`, and bounded
`evidence`. This control-plane exchange is not conversation history, summary, or
an Agent Loop turn.

`MemoryPolicy` verifies every source claim against canonical messages from the
current turn:

- `USER_EXPLICIT`: the exact quoted user statement must exist in the turn.
- `CONFIG_OBSERVED`: a successful current `read_file`, `search_text`, or
  `list_files` result must match the evidence path and ground the content.
- `TOOL_VERIFIED`: a successful current `execute_command` result must match the
  exact normalized command.
- `MODEL_INFERRED`: always ignored.

Only the local policy can decide `ADD`, `UPDATE`, or `IGNORE`; the model never
writes storage. User evidence outranks config evidence, which outranks verified
commands. A weaker source cannot replace a stronger same-key item. Equal or
stronger current evidence may update it while preserving ID and creation time.

## Safety and product behavior

Candidates and evidence are rejected if they contain credentials, known runtime
secrets, source/log dumps, oversized content, or transient debugging details.
Exact/normalized duplicates are ignored. Extraction, parsing, policy, or storage
failure does not alter the already-completed coding-task result and uses only a
generic non-secret diagnostic.

There is no automatic-memory approval dialog. Successful ADD/UPDATE operations
may appear as lightweight CLI/TUI activity; ignored items are silent. Manual
`/memory`, add, delete, and clear remain user-controlled. One-shot/background
behavior is not redesigned.

## Compatibility and exclusions

Old memory source values remain readable. Manual additions use
`USER_EXPLICIT`. No global or cross-workspace memory, embeddings, RAG, knowledge
graph, external memory service, reflection loop, autonomous source archive, or
new context architecture is introduced.

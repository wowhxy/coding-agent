# Memory Architecture Optimization Implementation Plan

**Goal:** 将 session summary 持久化，并建立受控、可确认、可测试的 workspace 长期记忆形成与统一 context 组合。

**Architecture:** Session JSON v3 内嵌 summary；Memory JSON v2 保留兼容 metadata；turn boundary extractor 只产生候选，InteractiveShell 掌握确认与持久化；ContextManager 维持确定性预算。

**Spec:** `docs/superpowers/specs/2026-08-28-memory-architecture-optimization-design.md`

**Global constraints:** Python 3.11+；无真实 API、Agent Framework、数据库、embedding/RAG、新 tools 或 Current Goal 概念；Git 写操作由 owner 管理；每 Task TDD + 一次 review。

## Task 1 — Persistent Session Summary

- **Files:** 修改 `summary.py`、`session.py`、`session_store.py`、`agent.py`、`interactive.py`、`scheduler.py`、CLI composition；扩展 `test_summary.py`、`test_session.py`、session/interactive tests。
- **Interfaces:** `SummaryState(text, covered_message_count, updated_at)`；`SessionRecord.summary`；`AgentRunner.summary_state` 与 `restore_summary_state()`。
- **TDD:** 先写 v1/v2 migration、v3 round-trip、invalid summary fallback、save/restart/resume/incremental 与 canonical-history 测试并确认失败；最小实现后运行 targeted tests。
- **Review:** coverage 合法性、secret redaction、interactive/background 原子保存、旧 session compatibility；修复一次后重跑 targeted tests。

## Task 2 — Workspace Memory Context Integration

- **Files:** 修改 `context.py`、`memory.py`、CLI/shell composition；扩展 context/workspace-memory/CLI tests。
- **Interfaces:** ContextManager 接收完整 memory text 并在 build 时按 latest user text 做 deterministic selection；memory context safe-render 回退。
- **TDD:** 先写 same-workspace `/new`/resume、history-summary 隔离、cross-workspace、restart、层顺序、8k budget、Unicode/empty/corrupt、>12 条 deterministic ranking 测试；实现后运行 Task 1–2 relevant regression。
- **Review:** workspace identity、不覆盖 corrupt file、anchors/latest turn 优先级与小 memory 稳定顺序。

## Task 3 — Candidate Extraction / Confirmation

- **Files:** 新建 `memory_candidate.py`；修改 `interactive_shell.py`、CLI composition；新增 candidate/shell tests。
- **Interfaces:** `MemoryCandidate(text, kind, source)`；`MemoryCandidateExtractor.extract(turn_messages)`；shell 在 FINAL_RESPONSE 后执行一次 bounded extraction/confirmation。
- **TDD:** 覆盖工具观察、用户长期约束、临时 debug 拒绝、最多 4 条、无候选静默、extraction failure、安全默认 N、accept/reject 与新 session 可见。
- **Review:** extraction 不进入 tool loop、不影响 Agent result、无确认绝不持久化、现有 scripted CLI 输入不被无故消费。

## Task 4 — Memory Quality / Safety

- **Files:** 修改 `memory.py`、candidate/shell；扩展 workspace-memory/candidate tests。
- **Interfaces:** Memory v2 metadata；`MemoryMatch(new|duplicate|conflict, existing)`；confirmed replace 保留 ID/created_at。
- **TDD:** 覆盖 v1 migration/v2 persistence、exact/normalized duplicate、topic conflict、replace accept/reject、API key/Bearer/token/password、超长/源码 dump 拒绝。
- **Review:** normalization 可解释、冲突范围克制、手动 add compatibility、明文限制诚实；随后运行 Task 3–4 relevant regression。

## Task 5 — Unified Budget / Offline Memory E2E

- **Files:** 收敛 `context.py`/composition，新增 `test_memory_architecture_e2e.py`，更新 `README.txt`。
- **Interfaces:** 固定 context：System → Existing Task → Workspace Memory → Persistent Summary → Recent Turns。
- **TDD/E2E:** 临时 C++ project + fake model/real local tools；Session A capture memory、长 history summary 持久化、restart/resume 恢复；`/new` Session B 只共享 workspace memory；第二 workspace 双向隔离。
- **Verification:** targeted E2E、full pytest、compileall、CLI help/import smoke、原 demo/MVP regression、placeholder/禁止框架/credential scan。
- **Final review:** 只做一次整体 requirement/correctness/architecture/regression/test/complexity/security review；统一一次 fix wave 后重新验证。

## Plan 自检

- Spec coverage：Task 1–5 分别覆盖 persistent summary、context integration、candidate、quality/safety、统一 E2E。
- Interface consistency：summary 始终 session-owned；memory 始终 workspace-owned；shell 独占用户授权；ContextManager 仅组合。
- 占位项：无未完成内容或未定义接口。
- Scope：不修改 tools 数量、Agent Framework、Current Goal、数据库或跨 workspace 语义。

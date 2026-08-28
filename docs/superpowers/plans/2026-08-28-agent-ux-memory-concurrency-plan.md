# Agent UX, Memory, and Concurrency Implementation Plan

**Goal:** 在不重写同步 Agent 核心的前提下完成 session UX、multiline、streaming、summary、workspace memory 和后台 session。

**Architecture:** `InteractiveShell` 统一交互命令；store/provider/context/scheduler 各自保持单一职责。所有新增测试离线确定性执行。

**Spec:** `docs/superpowers/specs/2026-08-28-agent-ux-memory-concurrency-design.md`

## Task 1 — Session CRUD 与 InteractiveShell

- **Goal:** 支持 `/new`、`/rename <name>`、`/delete`，保留原 turn transaction。
- **Files:** 新建 `interactive_shell.py`；修改 `interactive.py`、`session.py`、`session_store.py`、`cli.py`；新增/调整对应 tests。
- **Interfaces:** `InteractiveSession.execute(text) -> RunResult`；session/index v1 向 v2 兼容；store `rename_session`、`delete_session`。
- **Tests:** schema migration、rename persistence、删除回滚/最后 session、命令切换、原 `/exit` 和 one-shot 回归。
- **Done:** targeted tests 通过；一次简洁 review 无 Important 未修复问题。

## Task 2 — Session List / Search / Use

- **Goal:** `/sessions`、`/search <query>`、`/use <id>`，简洁可读 UI。
- **Files:** 修改 `session_store.py`、`interactive_shell.py`；新增 list/search tests。
- **Interfaces:** `SessionSummary`；`list_sessions(workspace)`；`search_sessions(workspace, query)`。
- **Tests:** updated_at 排序、Unicode casefold、名称/消息命中、空结果、损坏数据、workspace 隔离。
- **Done:** targeted tests 通过；一次 review 后无 Important。

## Task 3 — Multiline Input

- **Goal:** `/multiline` + `/send`/`/cancel`，保持单行兼容。
- **Files:** 修改 `interactive_shell.py` 和 shell tests。
- **Interfaces:** 私有 multiline reader 返回 `str | None`。
- **Tests:** 换行保持、空内容、cancel、Ctrl+C、EOF、后续单行正常。
- **Done:** targeted tests 通过；一次 review 后无 Important。

## Task 4 — Provider Streaming

- **Goal:** 文本实时输出，同时正确组装 tool calls 并保持 fallback。
- **Files:** 修改 `model.py`、`protocol.py`、`agent.py`、`openai_compatible.py`、`cli.py`、`tests/fakes.py`；新增 streaming tests。
- **Interfaces:** 可选 `complete_streaming(..., text_sink) -> ModelTurn`；`RunResult.streamed: bool = False`。
- **Tests:** SSE text chunks、Unicode、tool-call arguments fragments、malformed stream、transport failure、FakeModel fallback、无重复 final。
- **Done:** streaming targeted tests + 功能 1–4 full pytest + compile 通过；一次 review 后无 Important。

## Task 5 — Automatic Summary

- **Goal:** 超阈值时摘要较早历史，canonical history 不变，失败安全回退。
- **Files:** 新建 `summary.py`；修改 `context.py`、`agent.py`、CLI composition；新增 summary/context tests。
- **Interfaces:** `SummaryState`、`SummaryManager.prepare(history) -> SummaryState | None`；ContextManager 接收可选 summary。
- **Tests:** threshold、anchor/recent 保留、增量更新、history 不变、summary failure fallback、无 tool 调用摘要。
- **Done:** targeted tests 通过；一次 review 后无 Important。

## Task 6 — Workspace Memory

- **Goal:** 显式持久化、查看、删除、清空 workspace memory，并注入 context。
- **Files:** 新建 `memory.py`；修改 `interactive_shell.py`、`context.py`、CLI；新增 memory tests。
- **Interfaces:** `MemoryItem`、`WorkspaceMemoryStore.list/add/delete/clear/render`。
- **Tests:** workspace 隔离、原子持久化、Unicode、限制、删除/清空、key 脱敏、context 优先级。
- **Done:** targeted tests 通过；一次 review 后无 Important。

## Task 7 — Background / Concurrent Sessions 与 Final E2E

- **Goal:** 一个 session 后台运行时切换/使用另一个，失败与取消隔离。
- **Files:** 新建 `scheduler.py`；修改 `agent.py`、`interactive_shell.py`、`session_store.py`、CLI、README/Demo；新增 scheduler/E2E tests。
- **Interfaces:** `BackgroundScheduler.submit/list/cancel/shutdown`；`BackgroundJob` 状态；可选 `cancel_check`；`RunStatus.CANCELLED`。
- **Tests:** 独立 session/workspace、同 session busy 拒绝、index 锁、failure isolation、queued/cooperative cancellation、client close、完整离线 E2E。
- **Done:** targeted tests、功能 5–7 full regression、Final E2E、compile/help 通过；一次 Task review + 一次整体 Final Review，无未处理 Important。

## Plan 自检

- Requirement coverage：功能 1–7 分别映射 Task 1–7；阶段 regression 和 Final E2E 位于 Task 4/7。
- 顺序：shell/store → list/search → input → provider stream → context summary → memory → scheduler，依赖单向。
- 接口：InteractiveSession 单轮接口供 shell/scheduler 复用；summary 与 memory 只作为 context additions；后台 runner 独立。
- Scope：不新增框架、数据库、新工具或 async 核心；每 Task 仅 targeted tests 和一次 review。

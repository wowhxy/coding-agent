# Memory Architecture Optimization — Concise Design

状态：按负责人授权，自检通过后直接实现。基线：381 passed、6 skipped；同步 AgentRunner、六工具、Provider 与现有 CLI 保持稳定。

## 1. 当前架构与目标

当前分为 canonical `ConversationHistory`、仅进程内 `SummaryState`、workspace JSON memory 和负责裁剪的 `ContextManager`。本轮保持四层边界，但把 summary 变成 session-scoped persistent state，并为 workspace memory 增加受控 candidate/capture、质量校验与相关性选择。

三种实现路线中采用最小一致性方案：

- **采用：summary 内嵌 session JSON。** history 与 summary 同一次原子 session save，resume 不需要协调 sidecar。
- 不采用 summary sidecar：session 与 summary 双文件可能部分更新。
- 不采用 summary message：会污染 canonical history，并模糊 summary/history 边界。

## 2. Ownership 与持久化边界

- **Conversation History**：属于一个 session；保存完整 user/assistant/tool 协议记录；summary 和 context trimming 永不修改它。
- **Persistent Summary**：属于同一 session；是旧 history 的派生状态，不含 system prompt；随 session JSON 原子保存。
- **Workspace Memory**：属于 canonical workspace；同 workspace sessions 共享，不跨 workspace；保存在 session home 的 `memories/<workspace_hash>.json`。
- **ContextManager**：只组装一次请求，不拥有或持久化以上数据。

Session schema 从 v2 升级 v3，增加 `summary: null | {text, covered_message_count, updated_at}`。v1/v2 读取为 `summary=None`。summary 子对象损坏、coverage 小于 1 或超过 `len(messages)-1` 时只丢弃 summary，session/history 仍可恢复；下次正常保存写回合法 v3。

Memory schema 从 v1 升级 v2。条目增加 `kind`（command/constraint/convention/architecture/fact）、`source`（user/observed/confirmed_candidate）和 `updated_at`；v1 条目迁移为 `kind=fact`、`source=user`、`updated_at=created_at`，写入始终为 v2。

## 3. Summary lifecycle

超过 60,000 字符时，保留最近 8 个完整 turns，summary 只处理新进入 old-history 区域的消息。`covered_message_count` 相对 history 中“system + original user anchor”之后的消息计数；增量请求包含 previous summary 与新增 old messages，不从头重做。输出最多 8,000 字符、tool definitions 为空；tool call、空响应或 provider failure 保留旧 summary/无 summary，并继续当前 run。

`AgentRunner` 暴露只读 summary state，并可在 session activate 时恢复该 session 的 state。Interactive 与 background save 同时保存经过当前 key 脱敏的 summary。

## 4. Workspace memory lifecycle 与相关性

手动 `/memory` 命令继续存在。短 memory 直接按稳定存储顺序注入；超过 12 条时，按当前用户任务与最近用户消息的 normalized token/keyword overlap 排序，取 Top-12 并遵守 8,000 字符注入上限。同分按原顺序，算法离线、确定性，不使用 embedding/RAG。

Context 顺序固定为：system prompt → 现有 original user/task anchor → `[Workspace Memory]` → `[Session Summary]` → 最近完整 turns。总预算不足时先移除 summary，再移除 workspace memory，再淘汰较旧 recent turns；system、original task 与最新完整 turn 必须保留。单个 tool output 继续使用现有 20,000 字符上限。

Memory 文件损坏时，agent context 安全回退为空 memory，不阻止 session 恢复；显式 `/memory` 管理仍报告 `MEMORY_CORRUPT`，避免静默覆盖原文件。

## 5. Candidate / capture policy

仅在 interactive turn 以 `FINAL_RESPONSE` 正常结束后、且该 turn 包含工具证据或明确长期约束信号时执行一次提取。提取使用当前普通 model call、无 tools、严格 JSON，最多 4 条；失败或空候选不影响结果且不产生噪声。

候选必须通过本地校验：允许的 kind/source、1–500 字符、稳定项目事实；拒绝临时 debug/猜测、源码块/大段源码、巨大 tool output、当前 API key、Bearer/token/password/secret/credential 模式。模型不能直接写 memory。

每条候选显示后由用户 `[y/N]` 确认。新事实确认后保存为 `source=confirmed_candidate`；拒绝则无持久化。候选与现有条目精确或 normalized 重复时不再询问；简单同 topic 不同 value 视为 conflict，明确提示 replace，只有再次确认才原 ID 更新并刷新 `updated_at`。

## 6. Failure、安全与恢复

- summary 生成/解析/持久化状态损坏：保留 history，回退 deterministic trimming；
- memory context 损坏：该次按空 memory 运行，原文件不覆盖；
- candidate 提取/解析/验证失败：跳过，不改变 Agent result；
- candidate 未确认、冲突替换被拒：不写入；
- API key 与明显 credential 候选拒绝；手动 memory 继续过滤当前 key；
- JSON 仍是本地明文，不声明完整 DLP、OS sandbox 或跨进程锁。

## 7. 测试策略

全部使用 FakeModelClient、临时 workspace、fake clock/ID/input。Task 1 验证 session v1/v2/v3、summary restart/resume/incremental/corruption；Task 2 验证新 session 共享 memory、history/summary 隔离、顺序/预算/相关性；Task 3 验证候选边界与确认；Task 4 验证 metadata migration、dedup/conflict/secret；Task 5 完成 C++ workspace restart/new-session/cross-workspace 离线 E2E，并运行完整回归、compile 和 CLI smoke。

## 8. 明确排除

不设计 CurrentGoal/GoalManager；不做 global/cross-workspace memory、embedding、vector DB、RAG、知识图谱、数据库、Web UI、多 Agent、Agent Framework、自动源码归档、Tool Result archive、reflection loop 或无确认自主 memory 编辑。

## 9. 自检

- Coverage：四层 ownership、summary persistence、candidate/capture、quality、安全、budget 与 E2E 均有归属。
- Consistency：复用 SessionRecord/JsonSessionStore、SummaryManager、WorkspaceMemoryStore、ContextManager 与 FakeModelClient；不改核心 loop 协议。
- Migration：旧 session/memory 只读迁移，写入新版本；损坏派生 state 不阻断 canonical history。
- Scope：无 Current User Goal redesign、框架、数据库或新 coding tools。
- Ambiguity ruling：candidate 最多 4；长 memory 阈值 12/Top-12；conflict 替换保留原 ID/created_at；损坏 memory context 回退但不自动覆盖文件。

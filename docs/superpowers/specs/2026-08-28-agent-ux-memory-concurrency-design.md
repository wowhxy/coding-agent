# Agent UX, Memory, and Concurrency — Lightweight Design

状态：按负责人授权自检后执行  
基线：Python 3.11+；342 passed、6 skipped；同步 AgentRunner、六工具和一次性模式保持不变。

## 1. 接入方式

现有分层继续作为稳定核心：`AgentRunner → ModelClient → ToolRegistry`。本轮在外围增量增加：

- `InteractiveShell`：输入、命令、多行和活动 session 切换；
- `JsonSessionStore` 扩展：CRUD、list/search 和进程内锁；
- provider 可选 streaming 接口：provider 负责 SSE/tool-call 分片组装，AgentRunner 只消费规范化结果；
- `SummaryManager`：生成旧历史摘要，ContextManager 只负责组合有界请求；
- `WorkspaceMemoryStore`：与 session history 分离的 workspace 长期信息；
- `BackgroundScheduler`：线程 worker 包装同步 AgentRunner，不 async 重写核心。

`InteractiveSession` 保留每轮 working-copy/commit 语义，并新增单轮执行接口；原 `run()` 和 one-shot CLI 兼容。

## 2. CLI / 命令

- `/new`：创建并切换到未持久化的新 session；
- `/rename <name>`：重命名活动 session，名称去首尾空白、最多 80 字符；
- `/delete`：删除活动 session，再切换到最近剩余 session；没有则创建新 session；
- `/sessions`：按 `updated_at` 新到旧列出当前 workspace sessions；
- `/search <query>`：Unicode case-insensitive 搜索名称和完整持久化消息；
- `/use <session-id>`：切换到当前 workspace 的指定 session；
- `/multiline`：逐行输入，`/send` 提交，`/cancel` 或输入阶段 Ctrl+C 取消；空内容不调用模型；
- `/memory`、`/memory add <text>`、`/memory delete <id>`、`/memory clear`：显式管理 workspace memory；
- `/background <task>`、`/jobs`、`/cancel <job-id>`：后台 session 调度。

普通单行、`/exit`、CLI `--new-session/--resume-session` 继续工作。未知 `/command` 给出帮助，不发送模型。

## 3. 数据与接口

- Session schema 升级为 v2，新增 `name: str | None`；读取 v1 时迁移为 `None`，写入始终为 v2。
- Workspace index 升级为 v2，允许空 `session_ids` 和 `latest_session_id: null`；读取 v1，写入 v2。
- `SessionSummary(session_id, name, updated_at, is_latest)` 用于 list/search UI。
- Store 新增 `rename_session`、`delete_session`、`list_sessions`、`search_sessions`；所有公开操作在同一 `RLock` 下更新索引，保持当前进程内并发一致性。
- 删除采用 session 临时 tombstone → 原子 index 更新 → tombstone 删除；index 失败时恢复原文件。
- Streaming：可选 `complete_streaming(messages, tools, text_sink) -> ModelTurn`；不支持时回退 `complete`。`RunResult.streamed` 防止最终文本重复输出。
- Summary 是进程内 `SummaryState(text, covered_message_count)`，不修改/替换 canonical history。超过 60,000 字符阈值时只摘要较早历史，保留 system、第一 user goal 和最近完整轮；摘要失败回退现有确定性淘汰。
- Workspace memory 独立保存于 session home 的 `memories/<workspace_hash>.json`；单条最多 2,000 字符，总数最多 100，写入前脱敏当前 provider key。第一版不实现 global memory。
- Background job 使用独立 ModelClient/AgentRunner，最多 2 个 worker；同一 session 同时只运行一个 turn。取消在模型/工具步骤边界生效，阻塞中的复杂子进程仍遵循现有已知限制。

## 4. Context 边界

概念严格区分：

- Conversation History：session 原始完整协议记录；
- Context：一次模型请求实际发送的有界消息；
- Summary：旧 history 的派生压缩文本；
- Workspace Memory：跨 session 的显式项目长期信息；
- Global Memory：本轮不做。

Context 优先级：system + original user goal → workspace memory → summary → 最新 user-led turn → 可容纳的最近旧轮。Summary 或 memory 从不写回 ConversationHistory。

## 5. 错误、安全和并发

- session CRUD/search 对损坏记录返回现有稳定 `SessionError`，不静默跳过；
- rename/delete/search 的非法参数只产生简洁 CLI 错误并继续会话；
- API Key 不写入 session、summary、memory、job 结果或日志；
- storage 继续位于 workspace 外且严格校验；
- worker 失败只标记对应 job，不终止 shell 或其他 session；
- 进程退出时请求取消并有界关闭 worker；不承诺跨进程锁或强杀线程/孙进程。

## 6. 测试

全部普通测试离线：FakeModelClient、fake stream、MockTransport、tmp workspace、fake input/clock/ID。每个功能一个 targeted test 组；功能 1–4 后 full regression + compile，功能 5–7 后再次 full regression，最终执行覆盖 tool calling、streaming、summary、memory、多 session 的离线 E2E。

## 7. 明确不做

不加入 Agent Framework/SDK、Web UI、数据库/Vector DB/RAG、浏览器 Agent、分布式队列、多 Agent、新 coding tools、跨进程并发锁、自动长期偏好提取、global memory、复杂终端编辑器或 async Agent Loop 重写。

## 8. Design 自检

- Coverage：7 类功能、错误/安全/测试和阶段验证均有归属；
- Consistency：同步核心不变，新增接口均在外围或 provider/context 边界；
- Scope：无被排除依赖或功能；
- Ambiguity ruling：删除仅针对活动 session；多行使用 `/send`/`/cancel`；global memory 不实现；summary 第一版进程内缓存；并发为进程内 thread worker。

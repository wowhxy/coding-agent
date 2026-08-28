# Interactive Session Persistence Design Spec

日期：2026-08-27  
状态：已批准  
需求最高优先级来源：`推免考核题目学生版.pdf`  
基础设计：`docs/superpowers/specs/2026-08-27-coding-agent-design.md`

## 1. 目标与设计定位

本设计在已经完成的一次性命令行 Coding Agent 上增加本地交互式多轮会话和会话级持久化。用户进入一个 workspace 后，可以连续提交多个相关任务；每一轮继续使用同一份规范化 conversation history。退出进程后，再次进入同一 workspace 时默认恢复最近会话，也可以创建新会话或按 session ID 恢复旧会话。

这是一项后续增强，不改变 PDF 的核心考核目标。Agent Loop、tool definitions、tool dispatch、本地执行、provider parsing、context management、终止和错误处理仍由本项目自行实现。持久化只保存本地会话历史，不使用服务端 conversation、memory、文件或代码执行能力。

本设计中的 memory 明确定义为“用户显式选择的单个 session 内的持久化 conversation history”，不是自动长期记忆、跨项目知识库或模型生成摘要。

## 2. 成功标准

增强完成后，系统必须满足：

1. 无 task 参数启动时进入交互模式。
2. `FINAL_RESPONSE` 只结束当前用户轮次，CLI 随后继续等待输入。
3. 当前 workspace 默认恢复最近使用的 session。
4. `--new-session` 创建新 session，同时保留旧 session。
5. `--resume-session SESSION_ID` 只恢复属于当前 workspace 的指定 session。
6. `/exit`、输入阶段 `Ctrl+C` 和 EOF 可以正常退出。
7. 运行阶段 `Ctrl+C` 不提交未完成的当前轮。
8. session 文件与 workspace 索引按固定顺序分别原子替换，API Key 永不写入 session。
9. Context 仍受确定性预算控制，不因磁盘中保存完整历史而无限发送给模型。
10. 原有带 task 的一次性模式、六个本地工具和 OpenAI-compatible adapter 保持兼容。
11. 核心测试不调用真实 API，交互和持久化可通过 FakeModelClient、临时目录和假输入确定性测试。

## 3. 范围

### 3.1 包含

- 单进程、同步交互式 CLI；
- 每次一个活动 session；
- 同一 session 的多轮 user/assistant/tool history；
- workspace 级最近 session 自动恢复；
- 每个 workspace 保存多个 session；
- 新 session 和按 ID 恢复；
- JSON 文件持久化和 workspace 索引；
- 原子替换、schema 校验、损坏检测和稳定错误码；
- 当前 Provider API Key 的持久化前脱敏；
- 最新 system prompt 的恢复时注入；
- 回复语言默认跟随最新用户消息；
- 原有一次性模式兼容。

### 3.2 不包含

- 跨 workspace 长期 memory；
- 自动摘要、action journal、偏好提取或 `/remember`；
- 数据库；
- 服务端 conversation 或托管 memory；
- session 搜索、列表、重命名、删除或复杂管理 UI；
- 会话内 `/new`、`/resume` 等命令；
- 多行编辑器、流式输出或即时 Esc 捕获；
- 多进程并发写入或文件锁；
- session 文件加密；
- Web UI、多 Agent、Plugin system 或后台任务。

## 4. 用户体验与 CLI

### 4.1 一次性模式

现有调用继续执行一轮后退出，默认不创建持久化文件：

```powershell
python -m coding_agent --provider deepseek "修复测试失败并重新验证"
```

该模式继续使用现有退出码和自动化脚本语义。

### 4.2 交互模式

task 位置参数改为可选。省略 task 时进入持久化交互模式：

```powershell
Set-Location "D:\proj_demo"
python -m coding_agent --provider deepseek
```

启动输出：

```text
[run] workspace: D:\proj_demo
[run] provider: deepseek; model: deepseek-v4-flash
[session] resumed: a1b2c3d4e5f6
[session] enter /exit or press Ctrl+C to save and exit

you>
```

如果不存在最近会话，则输出 `created`。session ID 是经过冲突检查的 12 位小写十六进制字符串。

每轮模型最终回复使用 `agent>` 标记。`FINAL_RESPONSE` 是当前轮的协议终止，不退出交互进程：

```text
you> 运行测试并说明当前状态
[step 1] model requested: execute_command
[tool] execute_command: ok
[final] protocol status: FINAL_RESPONSE
agent> 当前三个测试均已通过。

you>
```

### 4.3 会话选择参数

```powershell
# 创建新 session，保留已有记录
python -m coding_agent --provider deepseek --new-session

# 恢复当前 workspace 的指定 session
python -m coding_agent --provider deepseek --resume-session a1b2c3d4e5f6
```

`--new-session` 与 `--resume-session` 互斥，并且只允许在无 task 的交互模式中使用。显式 session 不属于当前 workspace 时必须拒绝加载。

### 4.4 输入与退出

- 空白输入不调用模型，直接重新提示；
- 精确匹配、忽略首尾空白和大小写的 `/exit` 正常退出；
- 输入阶段 `Ctrl+C` 正常退出；
- EOF（Windows Ctrl+Z 或 POSIX Ctrl+D）等同正常退出；
- 模型或工具运行阶段 `Ctrl+C` 放弃当前工作副本，保留上一个持久化版本后退出；
- 第一版使用标准输入，不捕获即时 Esc。

API Key 在进程启动时最多安全询问一次。一个交互 session 复用同一个 ModelClient，并在退出路径统一关闭。

## 5. 组件与职责

```text
CLI composition
├── RuntimeConfig / Provider preset
├── JsonSessionStore
├── ConversationHistory
├── AgentRunner
└── InteractiveSession
    ├── input/output loop
    ├── per-turn history copy
    ├── persistence commit policy
    └── exit/error handling
```

### 5.1 AgentRunner

新增单轮 API：

`run_turn(self, history: ConversationHistory, user_message: str) -> RunResult`
执行且只执行一个用户轮次。

`run_turn` 在传入的 working history 上：

1. 追加 user message；
2. 执行现有有界 Agent Loop；
3. 追加 assistant tool-call message；
4. 串行执行工具并追加对应 tool result；
5. final response 出现时追加 final assistant message；
6. 返回当前轮 RunResult。

max steps、重复失败 fingerprint 和 stalled 计数每次 `run_turn` 重新初始化。

现有 `run(system_prompt, original_user_task)` 保留为兼容包装：创建新 history 后调用一次 `run_turn`。一次性模式的协议行为和退出状态不改变。

### 5.2 ConversationHistory

ConversationHistory 成为可复制、可从持久化消息恢复的规范化内存状态。它必须提供：

- 以当前 system prompt 创建空会话；
- 追加 Message；
- 返回不可变消息快照；
- 创建独立副本；
- 从不包含 system message 的持久化消息恢复，并在首位注入当前 system prompt；
- 验证首个持久化消息必须为 user message。

System prompt 是运行时安全策略，不属于用户 memory，因此不写入 session。恢复旧 session 时始终使用当前源码中的 `SYSTEM_PROMPT`。

### 5.3 InteractiveSession

InteractiveSession 只负责编排交互，不实现 Agent 推理或工具逻辑。它持有：

- AgentRunner；
- 当前已提交 ConversationHistory；
- JsonSessionStore；
- Session metadata；
- 可注入的输入函数和输出函数。

每轮执行前复制已提交 history。只有满足提交策略时才用 working history 替换 canonical history 并保存。

### 5.4 JsonSessionStore

JsonSessionStore 负责路径解析、ID 生成、序列化、严格校验、原子写入、workspace 索引和稳定错误。它不创建 Provider、不运行模型、不访问 workspace 文件，也不持有 API Key。InteractiveSession 在调用 store 之前创建仅用于持久化的脱敏消息副本；未脱敏的 canonical history 只保留在当前进程内存中。

用于测试的可变因素通过构造参数注入，包括 storage root、时钟和 session ID 生成器。不存在 plugin 或通用 persistence framework。

## 6. Context Management

磁盘保存完整规范化历史，但每次模型请求仍由 ContextManager 构建有界上下文。

永久锚点：

- 当前 system prompt；
- session 第一条 user message，即 original task。

轮次分组改为 user-led：

- 第一条 user 是永久锚点；其后的 assistant/tool 消息构成首轮尾部；
- 每一条后续 user message开始一个新轮；
- 该 user 后的 assistant/tool messages 属于同一轮，直到下一条 user；
- 多 tool calls 及其 tool results 不得被拆开。

上下文选择顺序：

1. 保留永久锚点；
2. 永久保留当前正在执行的最新 user-led 轮次；
3. 从新到旧加入最近若干完整历史轮次；
4. 超预算时只淘汰旧轮次，不丢弃最新用户请求；
5. 如果锚点与最新轮次本身超过预算，返回明确 ContextBudgetError，而不是向模型发送缺少当前任务的上下文。

工具结果在加入 canonical history 前继续使用现有确定性截断，因此磁盘和模型上下文保存的是同一截断版本。第一版不增加摘要、action journal 或 tokenizer 依赖。

## 7. 存储位置与数据模型

### 7.1 根目录

优先使用 `CODING_AGENT_HOME`。未设置时：

- Windows：`%LOCALAPPDATA%\coding-agent`；
- macOS：`~/Library/Application Support/coding-agent`；
- Linux：`$XDG_DATA_HOME/coding-agent`，未设置时使用 `~/.local/share/coding-agent`。

路径解析集中在单一函数中，测试始终注入 pytest 临时目录，不读取或写入真实用户 session。

### 7.2 文件布局

```text
coding-agent/
├── sessions/
│   └── <session_id>.json
└── workspaces/
    └── <workspace_hash>.json
```

workspace 使用已存在目录的 resolved path；Windows 额外使用 `normcase` 规范大小写。索引名是规范化路径 UTF-8 编码后的 SHA-256。session 内仍保存完整规范化 workspace，用于哈希碰撞和错误 workspace 校验。

### 7.3 Session schema v1

```json
{
  "schema_version": 1,
  "session_id": "a1b2c3d4e5f6",
  "workspace": "D:\\proj_demo",
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "created_at": "2026-08-27T13:00:00Z",
  "updated_at": "2026-08-27T13:05:00Z",
  "messages": [
    {"role": "user", "content": "检查项目"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "name": "list_files",
          "arguments_json": "{\"path\":\".\"}"
        }
      ]
    },
    {
      "role": "tool",
      "content": "{\"ok\":true,\"output\":\"duration.py\"}",
      "tool_call_id": "call-1"
    },
    {"role": "assistant", "content": "检查完成。"}
  ]
}
```

不保存 system prompt、API Key、Authorization header、完整环境变量或 HTTP 请求对象。

### 7.4 Workspace index schema v1

```json
{
  "schema_version": 1,
  "workspace": "D:\\proj_demo",
  "latest_session_id": "a1b2c3d4e5f6",
  "session_ids": ["112233aabbcc", "a1b2c3d4e5f6"]
}
```

第一版虽不提供列表 UI，仍保留 session_ids，避免创建新 session 时丢失旧记录关系，并为后续只读列表功能留下稳定数据。

## 8. 序列化与结构校验

反序列化必须严格校验：

- 根对象和 schema version；
- session ID 格式；
- workspace、provider、model 和 ISO UTC 时间字段；
- messages 必须为数组且首条为 user；
- session 文件中不得出现 system role；
- role 对应字段的类型与允许组合；
- assistant tool call 的 id/name/arguments_json；
- tool message 必须引用当前 assistant tool-call 批次中的 ID；
- 每个 tool call 恰好存在一个 tool result；
- 未知字段按 schema v1 的严格策略拒绝，避免静默误读未来格式。

合法的 terminal history 可以结束于：

- final assistant message；
- user message，例如模型请求前发生 API failure；
- 完整 tool-result 批次，例如 MAX_STEPS 或 STALLED。

不完整 tool-call 批次视为损坏记录。

## 9. 原子写入与提交策略

### 9.1 原子写入

每个 JSON 文件使用同目录临时文件：

```text
serialize UTF-8 JSON
→ write temporary file
→ flush
→ fsync
→ best-effort owner-only permission on POSIX
→ os.replace target
```

保存顺序固定为：

```text
session file
→ workspace index
```

因此索引不会指向尚未成功写入的 session。两个文件各自原子，但不声称组成跨文件数据库事务。若 session 已成功替换而索引更新失败，新 session 内容保持可读、旧索引保持完整，当前保存操作整体报告失败并停止交互，避免继续扩大两者差异。

### 9.2 每轮事务边界

```text
canonical history
→ copy to working history
→ AgentRunner.run_turn mutates working history
→ evaluate RunResult
→ redact known provider key
→ save session and index
→ replace canonical history
```

提交规则：

- FINAL_RESPONSE：提交并继续；
- MODEL_ERROR：提交结构完整的用户轮次和已有工具记录，报告错误并继续；
- MAX_STEPS：提交并继续；
- STALLED：提交并继续；
- INTERNAL_ERROR：不提交 working history，保留此前 canonical history；
- KeyboardInterrupt：不提交当前 working history并退出；
- session 文件替换前失败：旧 session 和旧索引保持完整，明确报告当前轮未持久化并退出；
- session 成功但 index 失败：保留已写入 session 和旧索引，明确报告索引未更新并退出。

新 session 在第一轮成功进入提交路径前可以只存在于内存。用户创建后立即退出不会留下空 session 文件。

## 10. Session 选择与恢复

默认交互启动：

1. 解析并规范化 workspace；
2. 读取 workspace index；
3. 有 latest session 时严格加载；
4. 无 index 时创建内存中新 session；
5. 索引或 latest session 损坏时不静默创建替代记录。

`--new-session` 忽略 latest 选择但不修改旧文件，直到新 session 首次成功保存后才成为 latest。

`--resume-session` 只接受严格 12 位 session ID。加载后必须校验 session workspace 与当前 workspace 相等。

Session metadata 保存创建和最近一次使用的 provider/model。恢复时使用本次 CLI 的 RuntimeConfig。provider 或 model 发生变化时输出警告，并在下一次成功保存后更新 metadata；内部 Message/ToolCall 协议保持 provider-neutral。

## 11. 错误模型与退出码

SessionStore 使用稳定机器错误码：

- `SESSION_NOT_FOUND`
- `SESSION_CORRUPT`
- `SESSION_VERSION_UNSUPPORTED`
- `SESSION_WORKSPACE_MISMATCH`
- `SESSION_INDEX_CORRUPT`
- `SESSION_IO_ERROR`
- `SESSION_SAVE_FAILED`

CLI 将交互初始化或保存错误映射为独立退出码 `7`。错误消息简洁、可恢复、无 traceback 和 API Key。

一次性模式原有退出码不变。交互模式中的 MODEL_ERROR、MAX_STEPS 和 STALLED 是轮次状态，不立即决定进程退出码；用户仍可继续输入。正常 `/exit`、EOF 或输入阶段 Ctrl+C 返回 0。

## 12. 安全与隐私

- SessionStore 位于 workspace 外，现有 WorkspacePaths containment 阻止 Agent 文件工具访问；
- execute_command 的 cwd 仍限制在 workspace，且过滤 Provider API Key 环境变量；
- API Key 只存在于 RuntimeConfig，字段继续 `repr=False`；
- 序列化前对所有字符串字段替换当前 Provider API Key 为 `[REDACTED]`；
- session 不保存环境变量、认证头或原始 provider payload；
- session 明文可能包含用户任务、源码片段和命令输出；README 必须提醒用户不要在对话中粘贴其他秘密；
- POSIX 权限尽量限制为当前用户，Windows 依赖用户数据目录 ACL；
- 第一版不声称提供加密、秘密扫描器或对任意凭据的自动识别。

## 13. 回复语言

System prompt 增加一条确定性行为要求：除非用户明确要求其他语言，否则 final response 使用最新用户消息的语言。CLI 不翻译、重写或二次调用模型处理 final response。

该规则改善默认体验，但模型输出仍属于概率行为；测试验证 system prompt 包含规则，不对真实模型语言能力作绝对保证。

## 14. 测试策略

### 14.1 AgentRunner 与 history

- run 兼容现有一次性行为；
- run_turn 追加 user、tool calls、tool results 和 final assistant；
- 两个连续 run_turn 使用相同 history；
- 每轮重置 max steps 和 stalled fingerprint；
- history copy 独立；
- INTERNAL_ERROR working history 可丢弃；
- 多 tool calls 配对保持正确。

### 14.2 ContextManager

- system 与首个 user 永久保留；
- 后续 user 开始新轮；
- 首轮尾部和后续完整轮次正确分组；
- 最近轮次选择顺序稳定；
- 最新用户轮次不被静默淘汰；
- 锚点与最新轮本身超预算时明确失败；
- tool call/result 批次不拆分；
- 超长 tool output 保持原有截断语义。

### 14.3 Serialization 与 store

- 所有 Message 和 ToolCall 字段往返；
- provider key 脱敏；
- system message 不持久化；
- 多 session、latest、new 和显式 resume；
- workspace 隔离和不匹配；
- ID 冲突重试；
- CODING_AGENT_HOME；
- schema、字段、角色顺序和 tool pairing 损坏；
- session 写入失败；
- session 成功但 index 失败；
- session 原子替换失败时旧 session 内容不变；
- session 成功但 index 失败时，新 session 可读且旧 index 内容不变。

### 14.4 InteractiveSession 与 CLI

- 无 task 进入交互模式；
- 有 task 保持一次性且不写 session；
- 多轮 FakeModelClient 脚本；
- FINAL_RESPONSE 后继续；
- 空输入、/exit、EOF、输入和运行阶段 Ctrl+C；
- MODEL_ERROR、MAX_STEPS、STALLED 后继续；
- INTERNAL_ERROR 回滚；
- persistence failure 退出 7；
- new/resume 参数冲突和模式限制；
- client 仅创建与关闭一次；
- provider/model 变化警告；
- 输出和文件均不含 API Key。

测试通过输入函数、输出 sink、临时 storage root、假时钟、假 ID generator 和 FakeModelClient 注入完成，不调用真实 API。

## 15. PDF 合规性与 Demo

该增强不使用 Agent Framework、现成 coding agent、服务端代码执行、服务端文件工具或托管 memory。Provider 仍只有现有 OpenAI-compatible adapter；核心 history、context、session orchestration、serialization 和 persistence 均由项目自行实现。

原有两分钟修复 Demo 继续使用一次性模式，避免持久化功能影响核心主线。若时间允许，可在交互模式中追加一个基于上一轮结果的追问，展示 history 连续性，但不得替代“查看项目—测试失败—修改—重新测试”的主要证据。

`README.txt` 仍需保持不超过 1000 汉字，并补充最短交互启动、恢复和本地明文 session 提示。视频和仓库中不得出现 API Key。

## 16. 已知限制

- session 明文且没有加密；
- 完整历史在磁盘上持续增长，没有压缩、摘要或自动清理；
- 每轮重写当前 session JSON，不适合超大历史；
- 不支持同一 session 的并发写入和文件锁；
- workspace 移动、重命名或路径别名不会自动迁移关联；
- 没有 session 列表、删除、搜索或重命名；
- 没有多行编辑器、即时 Esc 或流式输出；
- Context 使用字符预算而非精确 tokenizer；
- Provider/model 切换只警告，不保证不同模型对旧历史的行为完全一致；
- 只确定性脱敏当前已知 Provider API Key，无法识别所有用户秘密；
- Ctrl+C 对复杂命令孙进程树的行为仍受现有 execute_command 限制；
- protocol final 和持久化成功都不证明任务语义正确，仍需测试或其他验证证据。

## 17. 验收标准

1. 一次性模式现有测试全部保持通过。
2. 交互模式可在同一进程完成至少两个相关用户轮次。
3. 退出并重新启动后，可恢复同一 workspace 的最近 session。
4. 同一 workspace 可保存多个 session，并按 ID 恢复。
5. 不同 workspace 的 session 不能交叉恢复。
6. final assistant、tool calls 和 tool results 可完整往返持久化。
7. Context 在多轮 history 下保留锚点和最新用户轮，且预算行为确定。
8. 中断、内部错误、损坏文件和写入失败不会静默破坏上一份有效 session。
9. API Key 不出现在 session、日志、README 或测试 fixture 的真实凭据中。
10. FakeModelClient 可以离线确定性覆盖多轮交互、恢复和错误路径。
11. README、Demo 和面试说明能够明确区分当前轮终止、会话退出、持久化成功和语义正确性。

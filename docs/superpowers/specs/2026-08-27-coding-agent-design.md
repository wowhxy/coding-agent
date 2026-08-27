# Coding Agent Design Spec

日期：2026-08-27  
状态：正式批准  
需求最高优先级来源：`推免考核题目学生版.pdf`

## 1. 项目目标

本项目实现一个紧凑的命令行 coding agent。用户给出一个编程任务后，agent 与大语言模型多轮交互，由模型选择本地工具，agent 自行解析并分发工具调用，在指定 workspace 中读写文件、搜索文本、执行命令，并将工具结果反馈给模型，直到模型给出最终回复或运行被明确的保护条件终止。

核心目标按优先级排序如下：

1. 满足项目 PDF 的全部硬性要求。
2. 核心 agent 逻辑由本项目自行实现，不依赖 agent framework 或现成 coding agent。
3. 架构简单、可靠、容易测试和解释。
4. 在不调用真实付费 API 的情况下确定性测试核心流程。
5. 支持一个有说服力、可重复录制的两分钟 Demo。

成功不以功能数量衡量。MVP 的成功标准是：agent 能在一个真实的小型代码项目中查看文件、读取源码、运行测试、根据失败结果修改代码、再次运行测试，并在协议层正常返回最终回复。

## 2. 需求与合规边界

### 2.1 PDF 强制要求

| PDF 要求 | 本设计的覆盖方式 |
| --- | --- |
| 个人独立设计并实现 coding agent | 所有运行时核心模块均在本项目中实现；AI 仅辅助开发 |
| 与大语言模型交互 | 通过自有 `ModelClient` 抽象和一个 OpenAI-compatible adapter 交互 |
| 自主读写文件、执行命令、完成编程任务 | 由六个本地工具和显式 Agent Loop 实现 |
| 不包装现成 agent 产品 | CLI 直接运行本项目的 AgentRunner，不调用其他 coding agent |
| 不使用 agent framework / SDK | 不依赖 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等 |
| 可使用普通 API client、OpenAI-compatible API、原生 tool calling | MVP 使用普通 HTTP client 调用 OpenAI-compatible tool-calling 接口 |
| 不依赖服务端代码执行或文件工具 | 所有文件操作和命令执行均发生在本地进程 |
| 自行实现 history/context、工具、解析、终止、错误处理 | 分别由 `ConversationHistory`、`ContextManager`、`ToolRegistry`、provider parser 和 `AgentRunner` 实现 |
| 凭据不得进入仓库、README.txt 或视频 | 密钥只从环境变量读取；日志不输出密钥；命令子进程过滤 provider 密钥变量 |
| 语言和模型不限 | 选择 Python 3.11+；模型由 OpenAI-compatible 配置指定 |

PDF 允许功能很简单或很完善。本设计主动选择可完整测试和解释的 MVP，而不是以功能数量作为完成标准。

提交与过程约束也属于项目合规范围，但不由 agent runtime 实现：

- 公开仓库必须在题目发布后新建，GitHub 或 Gitee 均可。
- 保留真实完整提交历史，不压缩或改写已推送历史，截止后不再推送。
- 评委会结合提交时间和提交内容了解真实开发过程。
- `README.txt` 不超过 1000 汉字，包含仓库地址、运行方法和特色说明。
- 视频不超过 2 分钟，MP4 格式，不超过 200 MB，展示真实编程任务并简要讲解实现。
- 最终只提交以本人姓名命名的 ZIP，包含 `README.txt` 和视频。
- ZIP 提交至 PDF 指定的南京大学表单 `https://table.nju.edu.cn/dtable/forms/283d6c7d-475a-4f41-8baf-d3f45966ef2d/`；允许重复提交，以最后一次提交为准。
- 截止时间为 2026 年 9 月 2 日 24:00（北京时间）。
- 面试现场先播放视频，再简要介绍设计并回答提问；准备重点是解释 agent 的运行机制并为设计决策辩护。

Git 仓库及历史由项目负责人本人管理。Codex 可以报告适合提交的阶段、建议文件和 commit message，但不执行 Git 写操作。

### 2.2 明确禁止的运行时依赖

MVP 不得：

- 使用或包装 Claude Code、Codex、OpenCode、DeepSeek Harness 等现成 coding agent；
- 使用承担核心 Agent 逻辑的 Agent Framework / SDK；
- 调用服务端托管的代码执行、Code Interpreter、文件读写或 Files API 替代本地工具；
- 将 Superpowers 或其他 Codex 开发辅助能力作为最终 runtime dependency。

普通 HTTP、CLI、测试和数据校验库不属于 agent framework，但 MVP 仍优先减少依赖。计划的主要运行时第三方依赖只有 `httpx`；测试使用 `pytest`。

## 3. MVP 范围

### 3.1 包含

- Python 3.11+ 命令行程序；
- 单次用户任务的一次 agent run；
- 同步、单进程、显式循环；
- 一个 OpenAI-compatible provider adapter；
- 模型原生 Tool Calling；
- 六个本地工具；
- 内存中的 conversation history；
- 简单、确定性的 context window 管理；
- 最大步数、重复失败检测、命令超时、输出上限和结构化错误；
- FakeModelClient 驱动的离线测试；
- 可重复的真实编程任务 Demo。

### 3.2 不包含

除非后续测试提供必要性证据，MVP 不加入：

- Web UI；
- 数据库或持久化会话；
- 多 Agent；
- Plugin system；
- Docker 或完整 OS sandbox；
- 自动模型摘要 memory；
- action journal；
- 复杂 patch engine；
- 多 Provider 同时支持；
- 流式输出、并行工具执行或后台任务；
- 长期记忆、向量数据库或代码索引服务。

## 4. 总体架构

系统采用模块化单体。所有组件运行在同一个 Python 进程中，通过小型、显式的数据结构和接口通信。

```text
CLI
 └─ AgentRunner
     ├─ ConversationHistory
     ├─ ContextManager
     ├─ ModelClient
     │   ├─ OpenAICompatibleClient
     │   └─ FakeModelClient（tests）
     └─ ToolRegistry
         ├─ list_files
         ├─ search_text
         ├─ read_file
         ├─ write_file
         ├─ replace_in_file
         └─ execute_command
```

计划的代码边界如下：

```text
pyproject.toml
src/coding_agent/
  __main__.py          # CLI 入口和配置装配
  agent.py             # AgentRunner 和显式循环
  protocol.py          # Message、ToolCall、ToolResult、ModelTurn、RunResult
  context.py           # ConversationHistory 与 ContextManager
  model.py             # ModelClient Protocol
  providers/
    openai_compatible.py
  tools/
    registry.py
    files.py
    command.py
tests/
  fakes.py
  test_agent.py
  test_context.py
  test_tool_registry.py
  test_file_tools.py
  test_command_tool.py
README.txt
```

该结构表达逻辑边界，而不是要求每个文件都达到固定大小。实现时如果一个模块仅包含少量紧密相关代码，可以合并，但不得把 provider、tool execution 和 agent loop 混在同一个大文件中。

## 5. 核心数据模型

核心类型使用 Python `dataclass`、`Enum` 和 `typing.Protocol` 自行定义，不使用 agent SDK 类型。

```text
Message
  role: system | user | assistant | tool
  content: str | None
  tool_calls: list[ToolCall]
  tool_call_id: str | None

ToolCall
  id: str
  name: str
  arguments_json: str

ToolResult
  tool_call_id: str
  tool_name: str
  ok: bool
  output: str
  error_code: str | None
  error_message: str | None

ModelTurn
  final_text: str | None
  tool_calls: list[ToolCall]

RunResult
  status: FINAL_RESPONSE | MAX_STEPS | STALLED | MODEL_ERROR | INTERNAL_ERROR
  final_text: str | None
  steps: int
  error: str | None
```

Provider 的原始响应必须先转换为 `ModelTurn`，AgentRunner 不读取厂商原始 JSON。Tool handler 的异常必须转换为 `ToolResult`，模型看不到 Python traceback。

`ToolResult` 的三个结果字段职责明确分离：`output` 保存工具正常输出，或失败时仍有诊断价值的输出，例如 stdout/stderr；`error_code` 保存机器可识别、可测试的错误类型；`error_message` 保存供模型和用户理解失败原因并采取恢复行动的简洁说明。成功结果必须使用 `ok=true`、`error_code=None`、`error_message=None`；失败结果必须使用 `ok=false` 且同时提供非空 `error_code` 和 `error_message`，`output` 可以为空。

## 6. Model Provider

### 6.1 接口

`ModelClient` 只暴露一个同步方法：

```text
complete(messages, tool_definitions) -> ModelTurn
```

`AgentRunner` 只依赖该接口，因此测试可用 `FakeModelClient` 返回脚本化响应，无需网络或 API Key。

### 6.2 OpenAI-compatible adapter

MVP 只实现一个 OpenAI-compatible、非流式、支持原生 tool calling 的 adapter。它负责：

1. 将内部 `Message` 和 `ToolDefinition` 转换为请求 JSON；
2. 通过普通 HTTP client 发送请求；
3. 检查 HTTP 状态和响应基本结构；
4. 将 assistant content 和 tool calls 解析为 `ModelTurn`；
5. 将网络、状态码和协议错误转换为项目自有异常。

配置来源：

- `--base-url` 或非敏感环境变量；
- `--model` 或非敏感环境变量；
- `--api-key-env` 指定密钥所在环境变量的名称，默认 `OPENAI_API_KEY`；
- 不提供直接接收 API Key 明文的 CLI 参数，避免进入 shell history 或视频。

对应的配置变量名称为 `CODING_AGENT_BASE_URL` 和 `CODING_AGENT_MODEL`。base URL 与 model 都是必填项：CLI 参数优先于环境变量，任意一项最终缺失时都应启动失败并说明缺少哪项。密钥环境变量名本身不是秘密，密钥值只在 provider 初始化时读取。

Provider 对连接错误、请求超时以及 HTTP 408、429、500、502、503、504 最多额外重试 2 次；其他 4xx 不重试。超出后返回 `MODEL_ERROR`。请求不流式传输，不实现 provider fallback。

## 7. Conversation 与 Context Management

### 7.1 Canonical history

`ConversationHistory` 在内存中按发生顺序保存本次 run 的消息：

1. system prompt；
2. original user task；
3. 每次模型的 assistant message/tool calls；
4. 与每个 tool call ID 对应的 tool result。

完整 history 是运行状态；发给模型的 messages 由 `ContextManager` 从 history 中选择。MVP 不跨进程持久化会话。

### 7.2 MVP context 策略

第一版只实现确定性策略：

- 永久保留 system prompt；
- 永久保留 original user task；
- 保留最近若干个完整 model turn 及其关联 tool results，默认最近 8 个；
- file/search/command 的单次输出在写入 history 前确定性截断，默认最多 20,000 个字符；
- 总 context 字符预算默认 80,000 个字符，可通过 CLI 配置；
- 若仍超过总字符预算，按最旧到最新移除非锚点 turn，直到进入预算；
- system prompt 与 original user task 不被截断。若二者本身超过预算，启动失败并给出明确配置错误。

截断结果必须包含可见标记，说明原始长度、保留长度以及内容被截断，例如：

```text
[output truncated: original=48120 chars, kept=20000 chars]
```

统一截断算法先从总字符上限中扣除截断标记本身的长度，再把剩余容量分配给输出开头和结尾；奇数容量多出的一个字符分配给结尾。截断后的完整字符串（包括标记）不得超过上限。这样既保留命令和文件的开头背景，也保留通常位于末尾的错误摘要。

首版使用字符预算而不是依赖特定模型 tokenizer，以保持 provider-neutral 和依赖简单。该估算不精确，是已知限制。

action journal 和模型自动摘要均不属于 MVP。只有真实测试证明近期窗口不足时，才优先评估 deterministic action journal；模型摘要仍作为更后续的选择。

## 8. Tool System

### 8.1 ToolDefinition 与 Registry

每个工具包含：

```text
name
description
input_schema
handler
```

`input_schema` 是发送给模型的 JSON Schema。Registry 以名称查找工具，负责：

1. 检查工具是否存在；
2. 解析 `arguments_json`，并要求顶层是 JSON object；
3. 调用该工具自有的参数校验；
4. 调用 handler；
5. 将结果或异常规范化为 `ToolResult`。

unknown tool、非法 JSON、缺少字段、字段类型错误或未知字段都不会使进程崩溃，而是作为失败的 tool result 回传模型。参数校验逻辑由本项目实现，不依赖 agent framework。

若模型一次返回多个 tool calls，MVP 按响应顺序串行执行并分别回传结果。串行语义避免并行文件修改产生竞态，也使测试和答辩更直观。

### 8.2 六个本地工具

#### `list_files`

- 参数：相对 workspace 的目录 `path`，默认为 `.`；文件路径或不存在的路径返回错误。
- 递归返回该目录下按 workspace 相对路径稳定排序的文件和目录列表。
- 固定忽略 `.git`、`.venv`、`node_modules` 和 `__pycache__`。
- 最多返回 500 个条目，并在超限时返回截断标记。

#### `search_text`

- 参数：非空字面量 `query` 和相对 `path`；`path` 可以是单个文件或目录。
- 使用 Python 本地遍历实现，不依赖 `rg` 一定存在。
- 返回 `path:line_number:line_text`，结果稳定排序。
- 跳过忽略目录、包含 NUL 字节的二进制文件和大于 1 MiB 的单文件；最多返回 100 条匹配，并受统一输出上限约束。
- MVP 不支持正则表达式和代码索引。

#### `read_file`

- 参数：相对 `path`，可选从 1 开始且两端包含的 `start_line`、`end_line`；默认从第 1 行读到文件末尾。
- 只读取 workspace 内的 UTF-8 文本文件。
- 返回带行号内容，受统一输出上限约束。
- 缺失、目录、解码失败和越界路径返回结构化错误。

#### `write_file`

- 参数：相对 `path`、UTF-8 `content`、可选 `overwrite`，默认 `false`。
- 创建父目录不属于隐式行为；父目录缺失时失败，避免模型意外创建深层树。
- 文件已存在且 `overwrite=false` 时失败。
- 写入采用同目录临时文件加原子替换，尽量避免半写入状态。

#### `replace_in_file`

- 参数：相对 `path`、`old_text`、`new_text`。
- 默认且仅支持一次精确替换。
- `old_text` 出现 0 次或多于 1 次时失败，文件保持不变。
- 不实现 unified diff、模糊匹配或复杂 patch engine。
- 成功写入采用原子替换。

#### `execute_command`

- 参数：命令字符串和可选 `timeout_seconds`。
- 默认工作目录始终是 agent 启动时解析出的 workspace root；MVP 不允许命令指定其他 cwd。
- 命令字符串通过当前平台的默认 shell 同步执行；Agent 不在字符串层尝试实现不可靠的通用命令安全解析。
- 默认超时 30 秒；`timeout_seconds` 必须是 1 到 120 的整数。
- 捕获 stdout、stderr、exit code；非零退出作为结构化失败结果反馈模型。
- 超时后终止直接子进程并返回 `COMMAND_TIMEOUT`。
- stdout/stderr 受确定性输出上限约束。
- 子进程继承正常运行所需环境，但从环境副本中删除当前 provider 配置所使用的 API Key 变量，以及 `CODING_AGENT_SENSITIVE_ENV_NAMES` 中显式列出的其他变量。该配置只包含逗号分隔的环境变量名称，不包含密钥值。

`execute_command` 不是完整 OS sandbox。命令仍拥有启动 agent 的操作系统用户权限，可能访问 workspace 之外的系统资源。MVP 通过固定 cwd、超时、输出限制、密钥过滤和在可丢弃 workspace 中运行降低风险，但不声称提供强隔离。

### 8.3 Workspace path 安全

所有文件类工具在执行前：

1. 将用户路径相对已解析的 workspace root 组合；
2. 规范化并解析符号链接；
3. 验证最终路径仍位于 workspace root 内；
4. 对新文件验证其已解析父目录位于 workspace 内。

绝对路径、`..` 逃逸和指向 workspace 外部的符号链接均返回 `PATH_OUTSIDE_WORKSPACE`。

## 9. Agent Loop

AgentRunner 使用显式同步循环；一次 model request 计为一步，工具数量不额外计步。

```text
history.add(system_prompt)
history.add(original_user_task)

for step in 1..max_steps:
    messages = context_manager.build(history)
    model_turn = model_client.complete(messages, tool_definitions)

    if model_turn has tool_calls:
        history.add(assistant tool-call message)
        for call in tool_calls, in response order:
            result = registry.dispatch(call)
            history.add(tool result)
        continue

    if model_turn has non-empty final_text:
        return RunResult(FINAL_RESPONSE, final_text, step)

    return RunResult(MODEL_ERROR, malformed-response error, step)

return RunResult(MAX_STEPS, no final text, max_steps)
```

默认 `max_steps=20`，允许 CLI 配置为更小的正整数。它是无限循环的硬保护。

此外，若连续 3 次出现完全相同的 tool name、arguments 和失败结果，中间没有其他不同调用，AgentRunner 返回 `STALLED`。这只检测明显重复失败；任何情况下仍有最大步数兜底。

## 10. Protocol Termination 与语义正确性

本设计严格区分两个概念。

### 10.1 Protocol-level termination

以下条件之一会终止本次运行：

- 模型没有返回 tool call，且返回非空 final response：`FINAL_RESPONSE`；
- 达到最大 model steps：`MAX_STEPS`；
- 触发重复失败保护：`STALLED`；
- Provider/API/响应协议错误无法恢复：`MODEL_ERROR`；
- 未预期的本地内部错误：`INTERNAL_ERROR`。

`FINAL_RESPONSE` 只表示协议层正常结束，不表示编程任务在语义上已经正确完成。CLI 和 README 不得将该状态描述为自动证明“任务成功”。

### 10.2 Semantic task correctness

通用 agent 无法仅根据模型声称完成就证明任意编程任务正确。MVP 采用以下可解释的缓解措施：

- system prompt 明确要求模型在修改代码后，在条件允许时运行相关测试、构建、lint 或其他验证命令再结束；
- 最终回复应说明修改内容、执行过的验证及其实际结果；
- 工具历史保留命令退出码，用户可以核查测试证据；
- 测试不存在、无法运行或验证失败时，模型应明确报告，而不是声称成功。

MVP 不加入第二模型裁判、自动 completion verifier 或强制“必须运行测试”的通用规则，因为分析型任务、无测试项目和只读任务并不满足同一完成条件。

## 11. 错误处理

错误分为三层：

### 11.1 Tool-call 与本地工具错误

以下错误转换为 `ToolResult(ok=false)` 并反馈模型，使模型有机会恢复。每个失败结果都包含稳定的 `error_code` 和简洁的 `error_message`；如果执行过程还产生了有价值的信息，则保留在 `output`：

- `UNKNOWN_TOOL`
- `MALFORMED_ARGUMENTS`
- `PATH_OUTSIDE_WORKSPACE`
- `FILE_NOT_FOUND`
- `NOT_A_FILE`
- `DECODE_ERROR`
- `FILE_ALREADY_EXISTS`
- `EDIT_TARGET_NOT_FOUND`
- `EDIT_TARGET_AMBIGUOUS`
- `COMMAND_FAILED`，包含 exit code、stdout、stderr
- `COMMAND_TIMEOUT`

错误消息必须对模型有修复价值，但不得包含 API Key、HTTP 认证头或不必要的宿主绝对路径。

### 11.2 Provider 与模型协议错误

- 网络超时、连接失败和可重试状态码：有限重试；
- 认证失败、无效请求和持续服务错误：`MODEL_ERROR`；
- 缺失 choice/message、tool-call ID/name 等必需字段：`MODEL_ERROR`；
- tool arguments 是非法 JSON：视为 `MALFORMED_ARGUMENTS` tool result，而不是 provider 崩溃；
- 合法响应中既无 tool call 也无非空 final text：`MODEL_ERROR`。

### 11.3 内部错误

未预期异常在 CLI 边界转换为 `INTERNAL_ERROR` 和简洁诊断；默认不向模型或普通终端输出完整 traceback。开发测试可以启用 traceback，但不得包含密钥。

## 12. CLI 与可观察性

MVP 是一次性任务 CLI，而不是交互式聊天 UI。预期调用形式：

```text
python -m coding_agent --workspace PATH --model MODEL [options] "TASK"
```

CLI 在每一步输出简洁、可录屏的事件：

```text
[step 1] model requested: list_files
[tool] list_files: ok
[step 2] model requested: execute_command
[tool] execute_command: exit=1
...
[final] protocol status: FINAL_RESPONSE
```

默认不打印完整 provider 请求、响应对象、环境变量或认证信息。工具输出可显示截断后的正文，最终回复与协议状态分别标注。

若 API Key 曾被误写入仓库、`README.txt`、日志或视频，必须立即作废并更换；从后续提交中删除文本不能恢复已泄露凭据的安全性。

## 13. System Prompt 职责

System prompt 应短且明确，至少要求模型：

- 只使用提供的工具，不虚构工具结果；
- 先检查相关文件再修改；
- 优先做最小、针对性的编辑；
- 工具失败时读取错误并调整，不重复无效调用；
- 修改代码后，在条件允许时执行相关测试或验证命令；
- 只有不再需要工具时才给出 final response；
- 最终回复区分已验证事实、未验证内容和限制；
- 不主动搜索、显示或写入凭据。

Prompt 负责指导模型行为，但安全和循环终止不能只依赖 prompt，仍由本地代码强制执行路径检查、密钥过滤、超时和最大步数。

## 14. 测试策略

### 14.1 原则

- 核心测试不调用真实 API，不需要 API Key，不产生费用；
- AgentRunner 只依赖 `ModelClient` Protocol；
- 文件和命令测试使用 pytest 临时 workspace；
- 测试行为和状态，不断言脆弱的完整日志文本；
- 可选 live smoke test 默认跳过，且不得进入普通 CI。

### 14.2 FakeModelClient

FakeModelClient 接收预设的 `ModelTurn` 或异常序列，并记录每次收到的 messages 和 tool definitions。它用于证明：

- history 和 tool result 被正确反馈；
- 多步循环按预期推进；
- provider 与执行器之间没有隐藏的真实 API 依赖；
- max steps、错误终止和正常协议终止可确定性复现。

### 14.3 必测场景

Tool system：

- 正常 dispatch；
- unknown tool；
- 非法 JSON、非 object 参数、缺失字段、错误类型和未知字段；
- handler 异常被结构化。

File tools：

- 列目录、搜索、分段读取、创建文件、允许覆盖；
- missing file、解码失败、父目录缺失；
- `..`、绝对路径和符号链接逃逸；
- 精确替换成功、0 次和多次匹配失败；
- 失败编辑不改变原文件；
- 输出截断标记稳定。

Command tool：

- exit code 0；
- 非零 exit，保留 stdout/stderr；
- timeout；
- cwd 为 workspace；
- 普通环境变量可见；
- provider API Key 环境变量不可见；
- 输出截断。

Agent Loop：

- 单步 final response；
- tool call → result → final；
- 查看文件 → 测试失败 → 编辑 → 测试成功 → final 的完整多步脚本；
- 多 tool calls 串行和 ID 对应；
- tool failure 后模型恢复；
- max-step termination；
- 重复失败进入 `STALLED`；
- API failure 与 malformed model response；
- 空 final 不被视为正常终止；
- `FINAL_RESPONSE` 只表示协议状态，结果对象不含未经证明的 semantic-success 字段。

Context：

- system 和 original user task 永久保留；
- 最近 turn 保留；
- 旧 turn 按确定顺序淘汰；
- tool output 截断；
- 锚点本身超过预算时明确失败；
- 不存在 action journal 或自动摘要的隐式路径。

## 15. Demo 设计

Demo 使用一个小型、可丢弃、带自动化测试的本地项目，任务必须要求真实读取、诊断、修改和验证。推荐场景是一个有边界条件错误的 Python 函数，已有测试能稳定暴露错误。

预期轨迹：

```text
用户提出修复任务
→ list_files / search_text 查看项目
→ read_file 阅读源码与测试
→ execute_command 运行 pytest，得到确定性失败
→ replace_in_file 做最小修改
→ execute_command 再次运行 pytest，退出码 0
→ 模型给出 final response，CLI 标记 FINAL_RESPONSE
```

视频叙事控制在两分钟内：

- 前 15 秒：任务和 CLI 启动；
- 中间约 75 秒：展示 agent 的检查、失败测试、修改和再次测试；
- 最后约 30 秒：展示结果并用简图解释 Agent Loop、local dispatcher 和 FakeModel 测试。

允许剪辑等待模型的时间或加速播放。录制前应预热网络、固定 demo workspace、确认测试命令、清理终端中的敏感内容，并确保 API Key 不出现在命令行或输出中。

## 16. 已知限制

MVP 明确接受以下限制：

- 只支持一个 OpenAI-compatible tool-calling adapter；
- 不支持没有原生 tool calling 的纯文本模型；
- context 使用字符预算，不是精确 tokenizer；
- 只保留近期完整交互，不做语义摘要；
- UTF-8 文本文件优先，二进制和特殊编码不处理；
- exact replace 不适合大规模重构；
- command executor 不是 OS sandbox；
- 终止状态不能证明任务语义正确；
- 命令超时对复杂孙进程树的终止能力受操作系统限制；
- 单进程、串行工具执行，不适合长时间或高并发任务。

如果多一周，改进顺序为：根据真实失败数据增强 context/action journal；改善跨平台进程树终止；加入更强的 diff/patch 工具；再评估第二 provider。没有测试证据时不提前实现这些功能。

## 17. 验收标准

进入完成状态前，项目应满足：

1. 依赖清单中不存在 agent framework、现成 coding agent 或服务端代码/文件工具。
2. AgentRunner、history/context、provider parsing、tool schema/dispatch、本地执行、结果反馈、终止和错误处理均有本项目源码。
3. 六个工具及其主要失败路径有自动化测试。
4. FakeModelClient 可以离线跑通完整多步 Agent Loop。
5. max steps、command timeout、API failure 和 malformed response 有确定性测试。
6. 一个可丢弃 demo workspace 能展示失败测试、修改和成功测试。
7. live run 日志不包含 API Key，并将协议终止与语义正确性分开表述。
8. `README.txt` 满足 1000 汉字以内及内容要求。
9. 视频满足两分钟、MP4、200 MB 及真实编程任务要求。
10. 项目负责人能够解释每个核心模块、设计取舍、限制和后续改进方向。

## 18. 实现阶段决策原则

- 先写失败测试，再写满足该测试的最小实现；
- 每次只实现一个可验证的组件或垂直切片；
- 不因“以后可能需要”加入抽象层；
- 任何新增范围必须能对应 PDF 要求、已观察到的测试问题或 Demo 可靠性问题；
- 每个适合提交的阶段由 Codex 报告完成内容、建议文件和 commit message，Git 检查与提交由项目负责人本人完成。

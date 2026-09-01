项目：Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

架构图：
![Coding Agent 总体架构](CODING_AGENT_ARCHITECTURE.svg)

简介：
这是一个使用 Python 3.11+ 自主实现的本地 CLI/TUI Coding Agent。项目不使用 Agent Framework；基于模型原生 Tool Calling，自行实现 History、Context、ToolRegistry、本地执行、Agent Loop和错误处理。六个本地工具支持目录查看、文本搜索、文件读写、精确替换和命令执行，均在 workspace 内运行。

安装与运行：
python -m pip install -e .
coding-agent tui --provider deepseek

功能与体验：
TUI 提供多 Session 管理、恢复、搜索和重命名，将对话与 Tool、Plugin、Subagent Activity 分区展示。支持 Skills、Plugins、Workspace Memory 管理和历史 Recall。

核心特色：
1. Context/Memory：canonical History 保存完整协议事实；Context 在统一预算内组合 Skills、Workspace Memory、Persistent Summary、Recall 和最近完整轮次。采用渐进压缩：依次执行 Tool Result 截断、陈旧读取清理、Activity 压缩、增量摘要和按优先级渐进淘汰。Summary 属于单个 Session；Workspace Memory 跨同 workspace Session 共享，经用户或工具证据验证，并支持 Secret 过滤、去重、冲突更新和相关性检索。
2. Tool 并行：只并行连续且安全的只读调用；编辑、命令和控制工具构成串行屏障。结果按原 Tool Call 顺序反馈，兼顾速度与确定性。
3. Subagent：Explore、Analysis、Review 可并行执行独立只读调查，Child拥有独立 Context 和 Loop；父 Agent 保持单写者，统一修改和验证，避免并发写冲突。

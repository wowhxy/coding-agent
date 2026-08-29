项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：Python 3.11+；pip install -e .
python -m coding_agent --provider deepseek
python -m coding_agent --provider deepseek "<任务>"
coding-agent --provider deepseek "<任务>"

交互：默认恢复当前 workspace 的最近 session；--new-session 新建；--resume-session <ID> 恢复。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次。无已提交轮次的新会话不留下 session 文件。命令：/sessions /recall <query> /memory /background。

Context/Memory：渐进压缩、增量摘要；知识经确认后保存；相关选择和 workspace 隔离。Skill：只读 SKILL.md；/skills、/skill use；automatic 至多一次。Plugin：可信本地代码；/plugins、/plugin enable、/plugin disable；不是安全沙箱。

Subagent：delegate_tasks 单进程并行三个只读子任务；child 只有 list/search/read，parent 是单写者，负责编辑、命令和测试。

API Key：DeepSeek 使用 DEEPSEEK_API_KEY（默认 --thinking-mode disabled）；OpenAI 使用 OPENAI_API_KEY、--provider openai --model <模型>。密钥不入库。

特色：自实现 Agent Loop、六工具、Provider 抽象和离线测试。FINAL_RESPONSE 和持久化成功都不证明任务语义正确。

安全：execute_command 非完整 OS sandbox；本地 session JSON 是明文，可能含任务、源码和工具输出；不要粘贴秘密。不保证终止全部 descendant process。

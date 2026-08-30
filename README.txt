项目：终端 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：Python 3.11+；pip install -e ".[test]"
coding-agent tui --provider deepseek
python -m coding_agent --provider deepseek
python -m coding_agent --provider deepseek "<任务>"
coding-agent --provider deepseek "<任务>"

TUI：Ctrl+Enter提交，Ctrl+C取消；coding-agent doctor。

交互：默认恢复当前 workspace 的最近 session；--new-session 新建；--resume-session <ID> 恢复。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次。无已提交轮次的新会话不留下 session 文件。命令：/recall <query> /memory。
pointer/catalog/可重建FTS5；History在JSON。

Context/Memory：渐进压缩、增量摘要；知识经确认后保存；workspace 隔离。Skill：SKILL.md；/skills、/skill use；automatic。Plugin：/plugins、/plugin enable、/plugin disable；不是安全沙箱。

Subagent：delegate_tasks 单进程只读；parent 是单写者。

API Key：DEEPSEEK_API_KEY（--thinking-mode disabled）；OPENAI_API_KEY、--provider openai --model <模型>。密钥不入库。

特色：自实现 Agent Loop、六工具、Provider 抽象和 TUI。FINAL_RESPONSE 和持久化成功都不证明任务语义正确。

安全：execute_command 非 OS sandbox；本地 session JSON 是明文，含任务、源码和工具输出；不要粘贴秘密。详见 docs/tui-guide.md。

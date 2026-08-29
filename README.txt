项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：Python 3.11+；pip install -e .。
python -m coding_agent --provider deepseek
python -m coding_agent --provider deepseek "<任务>"
coding-agent --provider deepseek "<任务>"

默认恢复当前 workspace 的最近 session；--new-session 新建，--resume-session <ID> 恢复。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次。无已提交轮次的新会话不留下 session 文件。命令：/new /sessions /use /recall <query> /multiline /memory /background /jobs。

Context/Memory：渐进压缩、增量摘要；知识经确认后保存；去重、冲突更新、相关选择、workspace 隔离；Recall 临时。
Skill：读取 SKILL.md；/skills、/skill use；automatic 每任务至多一次，从属核心规则。
Plugin：可信代码：CODING_AGENT_HOME/plugins；/plugins、/plugin enable、/plugin disable。进程内执行，不是安全沙箱；见 docs/plugin-demo.md。

API Key：DEEPSEEK_API_KEY；默认 --thinking-mode disabled。OpenAI：OPENAI_API_KEY、--provider openai --model <模型>。
特色：自实现 Agent Loop、六工具、Provider 抽象、会话和离线测试。FINAL_RESPONSE 和持久化成功都不证明任务语义正确。
安全：密钥不入库；execute_command 不是完整 OS sandbox。本地 session JSON 是明文，可能含任务、源码和工具输出；不要粘贴秘密。不保证终止全部 descendant process。

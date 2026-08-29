项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：Python 3.11+；pip install -e . 后进入 workspace。
python -m coding_agent --provider deepseek "<任务>"
coding-agent --provider deepseek "<任务>"

交互：
python -m coding_agent --provider deepseek
默认交互启动恢复当前 workspace 的最近 session。--new-session；--resume-session <ID>。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次；无已提交轮次的新会话不留下 session 文件。
命令：/new、/sessions、/use、/recall <query>、/multiline、/memory、/background、/jobs。

Context/Memory：模型视图渐进压缩并持久化增量摘要。长期知识确认后才保存，支持去重、冲突更新、相关选择和 workspace 隔离；Recall 临时生效。

Skill：从两级 skills 目录读取 SKILL.md。/skills；/skill use；automatic 每任务至多一次且从属核心规则。

API Key：缺少 DEEPSEEK_API_KEY 时隐藏询问；默认 --thinking-mode disabled。OpenAI 使用 OPENAI_API_KEY、--provider openai --model <模型>。

特色：自实现 Agent Loop、六个工具、Provider 抽象、持久会话、离线测试。FINAL_RESPONSE 和持久化成功都不证明任务语义正确。
安全：密钥不入库；execute_command 不是完整 OS sandbox。本地 session JSON 是明文，可能包含任务、源码和工具输出；不要粘贴秘密。不保证终止全部 descendant process。

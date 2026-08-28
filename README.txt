项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：Python 3.11+；执行 pip install -e .，再进入 workspace。
python -m coding_agent --provider deepseek "<任务>"
coding-agent --provider deepseek "<任务>"

交互：
python -m coding_agent --provider deepseek
默认交互启动恢复当前 workspace 的最近 session；--new-session 新建，--resume-session <ID> 恢复。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次；无已提交轮次的新会话不留下 session 文件。

命令：/new、/rename <名称>、/delete；/sessions、/search <词>、/use <ID>；/multiline 后用 /send 或 /cancel；/memory、/memory add <内容>、/memory delete <id>、/memory clear；/background <任务>、/jobs、/cancel <job-id>。

无 DEEPSEEK_API_KEY 时隐藏询问 API Key；DeepSeek 默认 --thinking-mode disabled。OpenAI 使用 OPENAI_API_KEY、--provider openai --model <模型>。

特色：同步 Agent Loop、六个本地工具、流式输出、摘要、workspace memory、双后台线程、离线测试。FINAL_RESPONSE 和持久化成功都不证明任务语义正确，仍需测试证据。

安全：密钥不入库，memory 过滤当前 key。execute_command 不是完整 OS sandbox。本地 session JSON 是明文，memory 亦然，可能包含任务、源码和工具输出；不要粘贴秘密。后台取消仅在步骤边界生效，不保证强杀阻塞调用/descendant process，无跨进程锁。

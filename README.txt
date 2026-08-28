项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：安装 Python 3.11+，在源码目录执行 pip install -e .，再进入待处理 workspace。
一次性 DeepSeek：
python -m coding_agent --provider deepseek "<任务>"
PATH 已含 Python Scripts 时可用 coding-agent --provider deepseek "<任务>"。

交互：
python -m coding_agent --provider deepseek
默认交互启动恢复当前 workspace 的最近 session；--new-session 新建，--resume-session <ID> 恢复指定会话。/exit 或输入阶段 Ctrl+C 正常退出；运行阶段 Ctrl+C 丢弃未完成当前轮次；无已提交轮次的新会话不留下 session 文件。

若未设置 DEEPSEEK_API_KEY，程序隐藏询问 API Key；DeepSeek provider 默认使用 api.deepseek.com、deepseek-v4-flash、--thinking-mode disabled。OpenAI provider 需设置 OPENAI_API_KEY，并用 --provider openai --model <模型>。

特色：同步显式 Agent Loop、六个自实现本地工具、确定性上下文、步数/超时/结构化错误、离线 FakeModelClient 测试。FINAL_RESPONSE 和持久化成功都不证明任务语义正确，仍需测试等证据。

安全：密钥不写入仓库、README 或视频。execute_command 不是完整 OS sandbox。本地 session JSON 是明文，可能包含任务、源码和工具输出；不要粘贴秘密。

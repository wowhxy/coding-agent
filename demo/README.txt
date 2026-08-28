2 分钟 Demo 准备

1. 将 buggy_project 整个复制到一个可丢弃的临时目录，不要直接修改本目录中的 seed。
2. 在临时副本中运行 `python -m pytest -q`，确认结果恰好是 1 failed、2 passed。
3. 在源码目录执行一次 `pip install -e .`。进入临时副本后运行简化命令；若未设置密钥，CLI 会隐藏输入地询问。录屏前确认终端和命令历史不会显示密钥。
4. 将 agent 的 `--workspace` 指向临时副本，并提交任务：

请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。

5. Demo 应展示 agent 查看目录和源码、运行测试看到失败、修改 `duration.py`、再次运行测试得到 3 passed，并给出协议层 final response。
6. 强调 FINAL_RESPONSE 只表示 agent loop 正常结束；语义正确性证据来自第二次测试结果。

DeepSeek 首选调用（先进入临时副本）：
`python -m coding_agent --provider deepseek "请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。"`

若 Python Scripts 已加入 PATH，可将 `python -m coding_agent` 简写为 `coding-agent`。

DeepSeek 高级等价调用：
`python -m coding_agent --workspace <临时副本> --base-url https://api.deepseek.com --model deepseek-v4-flash --api-key-env DEEPSEEK_API_KEY --thinking-mode disabled "请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。"`

OpenAI 示例调用：
`python -m coding_agent --provider openai --model <OpenAI模型> "请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。"`

可选次要演示（不替代上述两分钟 one-shot 修复主线）：在同一临时副本运行不带任务的 `python -m coding_agent --provider deepseek`，先提问，再根据上轮回答追问一次，最后输入 `/exit`。同一 workspace 下次默认交互启动会恢复最近 session；仍须隐藏现场密钥，且持久化与协议 final 均不证明语义正确。

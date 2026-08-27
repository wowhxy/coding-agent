2 分钟 Demo 准备

1. 将 buggy_project 整个复制到一个可丢弃的临时目录，不要直接修改本目录中的 seed。
2. 在临时副本中运行 `python -m pytest -q`，确认结果恰好是 1 failed、2 passed。
3. 配置 OpenAI-compatible endpoint 和 API Key 环境变量；录屏前确认终端不会显示密钥。
4. 将 agent 的 `--workspace` 指向临时副本，并提交任务：

请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。

5. Demo 应展示 agent 查看目录和源码、运行测试看到失败、修改 `duration.py`、再次运行测试得到 3 passed，并给出协议层 final response。
6. 强调 FINAL_RESPONSE 只表示 agent loop 正常结束；语义正确性证据来自第二次测试结果。

示例调用：
`python -m coding_agent --workspace <临时副本> --base-url <兼容接口地址> --model <模型名> "请检查这个项目，运行测试定位失败原因，做最小修改修复问题，然后重新运行测试验证。不要修改测试。"`

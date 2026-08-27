项目：自实现的命令行 Coding Agent
Git 仓库：https://github.com/wowhxy/coding-agent

运行：安装 Python 3.11+，执行 pip install -e .，通过环境变量提供 API Key，然后运行 python -m coding_agent --workspace <项目目录> --base-url <兼容接口地址> --model <模型名> "<任务>"。

特色：同步显式 Agent Loop；六个自行实现的本地工具；确定性上下文截断；最大步数、超时和结构化错误；FakeModelClient 可离线测试多步工具调用。FINAL_RESPONSE 仅表示协议结束，不自动证明任务语义正确。

安全：密钥只通过环境变量提供，不写入仓库、README 或视频。execute_command 不是完整 OS sandbox，请在可信或可丢弃目录中运行。

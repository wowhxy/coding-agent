from __future__ import annotations

import re
from pathlib import Path


CREDENTIAL_LIKE_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def test_submission_readme_constraints() -> None:
    text = Path("README.txt").read_text(encoding="utf-8")

    assert len(text) <= 1000
    assert "http" in text
    assert "运行" in text
    assert "特色" in text
    assert "API Key" in text
    assert "OPENAI_API_KEY" in text
    assert "DEEPSEEK_API_KEY" in text
    assert "--thinking-mode disabled" in text
    assert 'python -m coding_agent --provider deepseek "<任务>"' in text
    assert 'coding-agent --provider deepseek "<任务>"' in text
    assert "python -m coding_agent --provider deepseek" in text.splitlines()
    assert "/exit" in text
    assert "Ctrl+C" in text
    assert "/exit 或输入阶段 Ctrl+C 正常退出" in text
    assert "运行阶段 Ctrl+C 丢弃未完成当前轮次" in text
    assert "无已提交轮次的新会话不留下 session 文件" in text
    assert "默认交互启动恢复当前 workspace 的最近 session" in text
    assert "--new-session" in text
    assert "--resume-session" in text
    assert "本地 session JSON 是明文" in text
    assert "任务、源码和工具输出" in text
    assert "不要粘贴秘密" in text
    assert "FINAL_RESPONSE 和持久化成功都不证明任务语义正确" in text
    assert "SKILL.md" in text
    assert "/skills" in text
    assert "/skill use" in text
    assert "automatic" in text
    assert "/recall <query>" in text
    assert "渐进压缩" in text
    assert "增量摘要" in text
    assert "确认后" in text
    assert "workspace 隔离" in text
    lines = text.splitlines()
    assert lines[1].startswith("Git 仓库：")
    repository_url = lines[1].removeprefix("Git 仓库：")
    assert repository_url.startswith(
        ("https://github.com/", "https://gitee.com/")
    )


def test_submission_sources_contain_no_credential_like_values() -> None:
    files = [
        *Path("src").rglob("*.py"),
        *Path("tests").rglob("*.py"),
        Path("README.txt"),
        Path("pyproject.toml"),
    ]
    matches: dict[str, list[str]] = {}
    for path in files:
        if not path.exists():
            continue
        found = CREDENTIAL_LIKE_PATTERN.findall(
            path.read_text(encoding="utf-8")
        )
        if found:
            matches[path.as_posix()] = found

    assert matches == {}

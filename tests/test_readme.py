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
    assert "CODING_AGENT_ARCHITECTURE.svg" in text
    assert Path("CODING_AGENT_ARCHITECTURE.svg").is_file()
    assert "python -m pip install -e ." in text
    assert "coding-agent tui --provider deepseek" in text
    assert "Agent Framework" in text
    assert "六个本地工具" in text
    assert "多 Session" in text
    assert "Skills" in text
    assert "Plugins" in text
    assert "渐进压缩" in text
    assert "增量摘要" in text
    assert "Workspace Memory" in text
    assert "Recall" in text
    assert "Tool 并行" in text
    assert "只读" in text
    assert "串行屏障" in text
    assert "Subagent" in text
    assert "单写者" in text
    assert "API Key" not in text
    assert "coding-agent doctor" not in text
    assert "demo/" not in text.casefold()
    lines = text.splitlines()
    assert lines[1].startswith("Git 仓库：")
    repository_url = lines[1].removeprefix("Git 仓库：")
    assert repository_url.startswith(
        ("https://github.com/", "https://gitee.com/")
    )


def test_plugin_demo_documents_trust_lifecycle_and_constrained_value() -> None:
    text = Path("docs/plugin-demo.md").read_text(encoding="utf-8")

    assert "trusted local code" in text
    assert "not an OS sandbox" in text
    assert "/plugins" in text
    assert "/plugin enable git-readonly" in text
    assert "/plugin disable git-readonly" in text
    assert "shell=False" in text
    assert "execute_command" in text
    assert "cleanup" in text.lower()


def test_subagent_demo_documents_parallel_single_writer_flow() -> None:
    text = Path("docs/subagent-demo.md").read_text(encoding="utf-8")

    assert "PowerShell" in text
    assert "delegate_tasks" in text
    assert "three" in text.casefold()
    assert "single process" in text.casefold()
    assert "single writer" in text.casefold()
    assert "read-only" in text.casefold()
    assert "DEEPSEEK_API_KEY" in text
    assert "[subagents] batch started: 3" in text
    assert "pytest" in text


def test_submission_sources_contain_no_credential_like_values() -> None:
    files = [
        *Path("src").rglob("*.py"),
        *Path("tests").rglob("*.py"),
        *Path("examples/plugins").rglob("*.py"),
        *Path("docs").rglob("*.md"),
        *Path("scripts").rglob("*.py"),
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

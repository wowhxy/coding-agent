from __future__ import annotations

import pytest

from coding_agent.session_title import (
    MAX_SESSION_TITLE_CHARS,
    generate_session_title,
)


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("请修复 Unicode parser 的测试失败", "修复 Unicode parser 的测试失败"),
        ("检查项目并运行全部测试", "检查项目并运行全部测试"),
        ("  请帮我\n检查\t pytest   测试。  ", "检查 pytest 测试"),
        ("请检查 GitHub API、TUI 与 C++ parser", "检查 GitHub API、TUI 与 C++ parser"),
    ],
)
def test_generate_session_title_is_deterministic_and_preserves_technical_terms(
    task: str,
    expected: str,
) -> None:
    assert generate_session_title(task) == expected
    assert generate_session_title(task) == expected


def test_generate_session_title_bounds_unicode_without_byte_splitting() -> None:
    title = generate_session_title("请帮我修复" + "解析器" * 30)

    assert len(title) == MAX_SESSION_TITLE_CHARS
    assert title.endswith("…")
    assert "�" not in title


@pytest.mark.parametrize("task", ["", " \t\n ", "# **...！！！**"])
def test_generate_session_title_falls_back_for_unusable_input(task: str) -> None:
    assert generate_session_title(task) == "Untitled"

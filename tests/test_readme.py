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

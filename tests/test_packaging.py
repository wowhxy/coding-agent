from __future__ import annotations

import tomllib
from pathlib import Path


def test_package_installs_coding_agent_console_command() -> None:
    configuration = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["project"]["scripts"] == {
        "coding-agent": "coding_agent.cli:main"
    }


def test_textual_runtime_and_tui_styles_are_packaged() -> None:
    configuration = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )

    assert "textual>=8,<9" in configuration["project"]["dependencies"]
    assert "tui/*.tcss" in configuration["tool"]["setuptools"]["package-data"][
        "coding_agent"
    ]

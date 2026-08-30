"""CLI and report entry points for the bounded benchmark suite."""

from __future__ import annotations

import argparse
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .common import (
    atomic_write_json,
    disposable_work_root,
    environment_metadata,
    utc_timestamp,
)
from .scenarios import run_initial_scenarios


_PROFILES = frozenset({"smoke", "standard"})


def build_report(*, profile: str, work_root: Path) -> dict[str, Any]:
    """Build one benchmark report."""

    if profile not in _PROFILES:
        raise ValueError(f"unknown benchmark profile: {profile}")
    with disposable_work_root(work_root) as root:
        scenarios = run_initial_scenarios(root, profile)
    return {
        "schema_version": 1,
        "suite": "coding-agent-v1",
        "profile": profile,
        "timestamp": utc_timestamp(),
        "environment": environment_metadata(),
        "scenarios": scenarios,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Persist one report."""

    atomic_write_json(path, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded Coding Agent benchmarks.")
    parser.add_argument("--profile", choices=sorted(_PROFILES), default="standard")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    arguments = parser.parse_args(argv)
    work_root = arguments.work_root or (
        Path(tempfile.gettempdir()) / f"coding-agent-benchmark-{uuid.uuid4().hex}"
    )
    report = build_report(profile=arguments.profile, work_root=work_root)
    write_report(arguments.output, report)
    failed = sum(item["status"] == "failed" for item in report["scenarios"])
    print(
        f"benchmark scenarios={len(report['scenarios'])} failed={failed} "
        f"output={arguments.output}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

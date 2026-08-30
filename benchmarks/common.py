"""Shared measurement, cleanup, and report helpers."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ScenarioMetrics = dict[str, object]


def timed(operation: Callable[[], Any]) -> tuple[Any, float]:
    """Return an operation result and elapsed wall-clock seconds."""

    started = time.perf_counter()
    result = operation()
    return result, time.perf_counter() - started


def measure(
    category: str,
    scenario: str,
    operation: Callable[[], ScenarioMetrics],
) -> dict[str, object]:
    """Measure one bounded scenario without making the full suite abort."""

    tracemalloc.start()
    started = time.perf_counter()
    try:
        metrics = operation()
        status = "passed"
        error = None
    except Exception as exc:  # benchmark result, not runtime error handling
        metrics = {}
        status = "failed"
        error = type(exc).__name__
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result: dict[str, object] = {
        "category": category,
        "scenario": scenario,
        "status": status,
        "elapsed_seconds": round(elapsed, 6),
        "peak_bytes": peak,
        "metrics": metrics,
    }
    if error is not None:
        result["error"] = error
    return result


def environment_metadata() -> dict[str, object]:
    """Return non-sensitive environment facts needed to compare reports."""

    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable_name": Path(sys.executable).name,
        "cpu_count": os.cpu_count(),
    }


@contextmanager
def disposable_work_root(path: Path) -> Iterator[Path]:
    """Create and remove one exact benchmark-owned directory."""

    target = Path(path).absolute()
    if target.exists():
        raise ValueError(f"benchmark work root already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    try:
        yield target
    finally:
        shutil.rmtree(target, ignore_errors=True)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Write a UTF-8 JSON report through a same-directory atomic replace."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

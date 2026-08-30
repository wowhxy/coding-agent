"""Manual structural benchmark for Session Management v2.

This reports measured timings without imposing CI millisecond thresholds.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

from coding_agent.protocol import Message, Role
from coding_agent.session import deserialize_session
from coding_agent.session_index import SessionIndex
from coding_agent.session_store import JsonSessionStore


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    sessions: int
    canonical_bytes: int
    index_bytes: int
    legacy_latest_seconds: float
    pointer_latest_seconds: float
    history_list_seconds: float
    catalog_list_seconds: float
    scan_search_seconds: float
    fts_search_seconds: float


def benchmark(size: int, root: Path) -> BenchmarkResult:
    workspace = root / f"workspace-{size}"
    home = root / f"home-{size}"
    workspace.mkdir(parents=True)
    counter = 0

    def clock() -> datetime:
        nonlocal counter
        counter += 1
        return datetime(2026, 8, 30, tzinfo=timezone.utc) + timedelta(seconds=counter)

    identifiers = iter(f"{index:012x}" for index in range(1, size + 1))
    store = JsonSessionStore(home, clock=clock, id_generator=lambda: next(identifiers))
    for index in range(size):
        marker = " benchmark_unique_marker" if index == size // 2 else ""
        store.save(
            replace(
                store.create_session(workspace, "fake", "model"),
                messages=(
                    Message(Role.USER, f"task {index}{marker} " + "x" * 400),
                    Message(Role.ASSISTANT, f"completed {index}"),
                ),
            )
        )

    session_paths = tuple((home / "sessions").glob("*.json"))
    canonical_bytes = sum(path.stat().st_size for path in session_paths)
    index_path = SessionIndex(home, workspace.resolve()).database_path

    legacy_latest_seconds = _elapsed(lambda: _legacy_records(session_paths, workspace)[-1])
    pointer_latest_seconds = _elapsed(lambda: JsonSessionStore(home).load_latest(workspace))
    history_list_seconds = _elapsed(lambda: _legacy_records(session_paths, workspace))
    catalog_list_seconds = _elapsed(
        lambda: JsonSessionStore(home).list_sessions(workspace, limit=50)
    )
    scan_search_seconds = _elapsed(
        lambda: JsonSessionStore(home).search_session_results(
            workspace, "benchmark_unique_marker", fts_enabled=False
        )
    )
    fts_search_seconds = _elapsed(
        lambda: JsonSessionStore(home).search_session_results(
            workspace, "benchmark_unique_marker"
        )
    )
    return BenchmarkResult(
        size,
        canonical_bytes,
        index_path.stat().st_size,
        legacy_latest_seconds,
        pointer_latest_seconds,
        history_list_seconds,
        catalog_list_seconds,
        scan_search_seconds,
        fts_search_seconds,
    )


def _legacy_records(paths: tuple[Path, ...], workspace: Path):
    records = [
        deserialize_session(path.read_text(encoding="utf-8")) for path in paths
    ]
    records = [record for record in records if record.workspace == workspace.resolve()]
    return sorted(records, key=lambda record: (record.updated_at, record.session_id))


def _elapsed(operation) -> float:
    operation()  # warm filesystem/SQLite caches so ordering does not dominate
    samples = []
    for _ in range(3):
        started = perf_counter()
        operation()
        samples.append(perf_counter() - started)
    return min(samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=(100, 1000))
    arguments = parser.parse_args(argv)
    if any(size <= 0 for size in arguments.sizes):
        parser.error("sizes must be positive")
    with tempfile.TemporaryDirectory(prefix="coding-agent-session-benchmark-") as value:
        for size in arguments.sizes:
            print(json.dumps(asdict(benchmark(size, Path(value))), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

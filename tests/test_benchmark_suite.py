from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.run import build_report, write_report


@pytest.fixture(scope="module")
def smoke_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("benchmark-suite")
    work_root = root / "work"
    report = build_report(profile="smoke", work_root=work_root)
    assert not work_root.exists()
    return report


def test_smoke_benchmark_covers_core_tools_subagents_and_context_without_leaks(
    smoke_report: dict[str, object],
) -> None:
    report = smoke_report

    assert report["schema_version"] == 1
    assert report["profile"] == "smoke"
    scenarios = report["scenarios"]
    assert {item["category"] for item in scenarios} >= {
        "core_agent_loop",
        "tool_execution",
        "subagents",
        "context_scaling",
        "session_scaling",
        "search_fts",
        "memory_summary",
        "tui_product",
        "memory_usage",
        "coding_tasks",
        "bounded_stress",
    }
    assert all(item["status"] in {"passed", "skipped"} for item in scenarios)
    assert all(item["elapsed_seconds"] >= 0 for item in scenarios)
    assert all(item["peak_bytes"] >= 0 for item in scenarios)


def test_scaling_scenarios_report_real_backends_and_bounded_state(
    smoke_report: dict[str, object],
) -> None:
    report = smoke_report
    by_category = {item["category"]: item for item in report["scenarios"]}

    sessions = by_category["session_scaling"]["metrics"]
    assert sessions["sizes"][0]["session_files_loaded_for_list"] == 0
    assert sessions["sizes"][0]["latest_fast_path"] is True
    assert sessions["sizes"][0]["startup_seconds"] > 0
    search = by_category["search_fts"]["metrics"]
    assert search["search_backend"] in {"fts5", "scan"}
    assert search["canonical_history_bytes"] > 0
    memory = by_category["memory_summary"]["metrics"]
    assert memory["incremental_new_messages"] < memory["total_old_messages"]
    tui = by_category["tui_product"]["metrics"]
    assert tui["no_crash"] is True
    assert tui["conversation_items"] > 0
    assert tui["activity_chars"] > 0
    assert tui["model_calls"] == {
        "parent": 1,
        "skill_selector": 0,
        "summary": 0,
        "memory_candidate": 0,
        "subagent": 0,
        "total": 1,
    }


def test_disposable_coding_tasks_report_semantic_verification(
    smoke_report: dict[str, object],
) -> None:
    report = smoke_report
    coding = next(
        item for item in report["scenarios"] if item["category"] == "coding_tasks"
    )

    tasks = coding["metrics"]["tasks"]
    assert {item["name"] for item in tasks} == {
        "simple_python_bugfix",
        "multi_file_python_bugfix",
        "add_tests",
        "read_only_code_review",
        "create_project",
        "cpp_compile_test",
        "repository_exploration",
        "subagent_investigation",
    }
    assert all(item["status"] in {"passed", "skipped"} for item in tasks)
    assert all(
        item["verification_passed"] is True
        for item in tasks
        if item["status"] == "passed"
    )


def test_bounded_stress_scenario_completes_all_fixed_operation_counts(
    smoke_report: dict[str, object],
) -> None:
    stress = next(
        item for item in smoke_report["scenarios"]
        if item["category"] == "bounded_stress"
    )["metrics"]

    assert stress == {
        "tool_calls": 50,
        "session_switches": 40,
        "session_searches": 20,
        "cancelled_runs": 50,
        "subagent_batches": 10,
        "subagents_completed": 30,
        "plugin_enable_disable_cycles": 30,
    }


def test_benchmark_report_is_written_as_valid_machine_readable_json(
    tmp_path: Path,
    smoke_report: dict[str, object],
) -> None:
    output = tmp_path / "nested" / "result.json"

    write_report(output, smoke_report)

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["environment"]["python_version"]
    assert saved["scenarios"]
    assert not tuple(output.parent.glob(".result.json.*.tmp"))


def test_unknown_benchmark_profile_is_rejected_before_work_is_created(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "work"

    try:
        build_report(profile="unbounded", work_root=work_root)
    except ValueError as exc:
        assert str(exc) == "unknown benchmark profile: unbounded"
    else:
        raise AssertionError("unknown profile was accepted")

    assert not work_root.exists()

"""Offline benchmark scenarios A-D for core runtime behavior."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.application.events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
)
from coding_agent.application.service import CodingAgentService
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.context_policy import ContextPolicy
from coding_agent.memory import WorkspaceMemoryStore
from coding_agent.protocol import (
    Message,
    ModelTurn,
    Role,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from coding_agent.recall import RecallService
from coding_agent.session_index import SessionIndex
from coding_agent.session_store import JsonSessionStore
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import SubagentRequest, SubagentRole, SubagentTask
from coding_agent.summary import SummaryState
from coding_agent.summary import SummaryManager
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import ActivityPane, ConversationPane
from coding_agent.tool_execution import ToolExecutionScheduler
from coding_agent.tools.files import (
    create_list_files_tool,
    create_read_file_tool,
    create_search_text_tool,
)
from coding_agent.tools.paths import WorkspacePaths
from coding_agent.tools.registry import RegisteredTool, ToolEffect, ToolRegistry

from .common import measure, timed
from .coding_tasks import run_coding_tasks
from .fakes import FakeModelClient
from .stress import run_bounded_stress


def run_initial_scenarios(root: Path, profile: str) -> list[dict[str, object]]:
    """Run benchmark categories A-I."""

    scenarios = [
        measure("core_agent_loop", "fake_model_steps", _core_agent_loop),
        measure(
            "tool_execution",
            "builtins_and_parallel_scheduler",
            lambda: _tool_execution(root / "tools", profile),
        ),
        measure(
            "subagents",
            "three_independent_investigations",
            lambda: _subagents(root / "subagents", profile),
        ),
        measure(
            "context_scaling",
            "progressive_compression",
            lambda: _context_scaling(profile),
        ),
        measure(
            "session_scaling",
            "catalog_latest_and_crud",
            lambda: _session_scaling(root / "sessions", profile),
        ),
        measure(
            "search_fts",
            "scan_fts_and_rebuild",
            lambda: _search_fts(root / "sessions", profile),
        ),
        measure(
            "memory_summary",
            "retrieval_incremental_summary_and_restart",
            lambda: _memory_summary(root / "memory-summary"),
        ),
        measure(
            "tui_product",
            "startup_render_activity_switch_and_resize",
            lambda: _tui_product(root / "tui"),
        ),
    ]
    coding = measure(
        "coding_tasks",
        "disposable_offline_tasks",
        lambda: run_coding_tasks(root / "coding-tasks"),
    )
    if coding["status"] == "passed" and any(
        item["status"] == "failed" for item in coding["metrics"]["tasks"]
    ):
        coding["status"] = "failed"
        coding["error"] = "SemanticVerificationFailed"
    scenarios.append(coding)
    scenarios.append(
        measure(
            "bounded_stress",
            "repeated_bounded_operations",
            lambda: run_bounded_stress(root / "stress"),
        )
    )
    scenarios.append(
        measure(
            "memory_usage",
            "representative_scenario_peaks",
            lambda: {
                "peak_bytes_by_category": {
                    item["category"]: item["peak_bytes"] for item in scenarios
                },
                "bounded_scenarios": len(scenarios),
            },
        )
    )
    return scenarios


def _core_agent_loop() -> dict[str, object]:
    cases = ((1, 0), (2, 1), (5, 4), (10, 9))
    observations: list[dict[str, object]] = []
    total_model_calls = 0
    total_tool_calls = 0
    for expected_steps, requested_tools in cases:
        registry = _probe_registry()
        script = [
            ModelTurn(tool_calls=(ToolCall(f"call-{index}", "probe", "{}"),))
            for index in range(requested_tools)
        ] + [ModelTurn("complete")]
        client = FakeModelClient(script)
        runner = AgentRunner(client, registry, ContextManager(), max_steps=12)
        result, elapsed = timed(lambda: runner.run("system", "task"))
        if result.status is not RunStatus.FINAL_RESPONSE or result.steps != expected_steps:
            raise AssertionError("core AgentRunner scenario did not complete")
        total_model_calls += len(client.calls)
        total_tool_calls += runner.last_tool_execution_stats.tool_calls_total
        observations.append(
            {
                "steps": result.steps,
                "model_calls": len(client.calls),
                "tool_calls": runner.last_tool_execution_stats.tool_calls_total,
                "context_builds": len(client.calls),
                "elapsed_seconds": round(elapsed, 6),
            }
        )

    multi_calls = tuple(ToolCall(f"multi-{index}", "probe", "{}") for index in range(3))
    client = FakeModelClient([ModelTurn(tool_calls=multi_calls), ModelTurn("complete")])
    runner = AgentRunner(client, _probe_registry(), ContextManager())
    result, elapsed = timed(lambda: runner.run("system", "multi tool task"))
    if result.status is not RunStatus.FINAL_RESPONSE:
        raise AssertionError("multiple ToolCalls scenario did not complete")
    observations.append(
        {
            "steps": result.steps,
            "model_calls": len(client.calls),
            "tool_calls": runner.last_tool_execution_stats.tool_calls_total,
            "context_builds": len(client.calls),
            "multiple_tool_calls": True,
            "elapsed_seconds": round(elapsed, 6),
        }
    )
    return {
        "cases": observations,
        "model_calls": total_model_calls + len(client.calls),
        "tool_calls": total_tool_calls + runner.last_tool_execution_stats.tool_calls_total,
    }


def _probe_registry(*, delay_seconds: float = 0) -> ToolRegistry:
    registry = ToolRegistry()

    def handle(call_id: str, _arguments: dict[str, object]) -> ToolResult:
        if delay_seconds:
            time.sleep(delay_seconds)
        return ToolResult(call_id, "probe", True, "ok")

    registry.register(
        RegisteredTool(
            ToolDefinition(
                "probe",
                "Return one deterministic benchmark observation.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            lambda arguments: arguments,
            handle,
            effect=ToolEffect.READ_ONLY,
            parallel_safe=True,
        )
    )
    return registry


def _tool_execution(root: Path, profile: str) -> dict[str, object]:
    root.mkdir()
    (root / "a.txt").write_text("needle\n" * 200, encoding="utf-8")
    (root / "b.txt").write_text("other\n" * 200, encoding="utf-8")
    paths = WorkspacePaths(root)
    registry = ToolRegistry()
    registry.register(create_list_files_tool(paths))
    registry.register(create_search_text_tool(paths))
    registry.register(create_read_file_tool(paths))
    calls = (
        ToolCall("read-a", "read_file", json.dumps({"path": "a.txt"})),
        ToolCall("read-b", "read_file", json.dumps({"path": "b.txt"})),
        ToolCall("search", "search_text", json.dumps({"path": ".", "query": "needle"})),
    )
    singles: dict[str, float] = {}
    for call in (
        calls[0],
        calls[2],
        ToolCall("list", "list_files", json.dumps({"path": "."})),
    ):
        result, elapsed = timed(lambda call=call: registry.dispatch(call))
        if not result.ok:
            raise AssertionError(f"built-in tool failed: {call.name}")
        singles[call.name] = round(elapsed, 6)

    serial_results, serial_elapsed = timed(
        lambda: tuple(registry.dispatch(call) for call in calls)
    )
    scheduler = ToolExecutionScheduler(registry)
    outcome, parallel_elapsed = timed(lambda: scheduler.execute(calls))
    if outcome.results != serial_results or not all(result.ok for result in outcome.results):
        raise AssertionError("parallel tool scheduler changed ordered semantics")

    delay = 0.01 if profile == "smoke" else 0.03
    delayed_registry = _probe_registry(delay_seconds=delay)
    delayed_calls = tuple(ToolCall(f"delay-{index}", "probe", "{}") for index in range(3))
    _serial, controlled_serial = timed(
        lambda: tuple(delayed_registry.dispatch(call) for call in delayed_calls)
    )
    _parallel, controlled_parallel = timed(
        lambda: ToolExecutionScheduler(delayed_registry).execute(delayed_calls)
    )
    return {
        "single_elapsed_seconds": singles,
        "serial_equivalent_seconds": round(serial_elapsed, 6),
        "parallel_scheduler_seconds": round(parallel_elapsed, 6),
        "observed_disk_speedup": _speedup(serial_elapsed, parallel_elapsed),
        "controlled_io_serial_seconds": round(controlled_serial, 6),
        "controlled_io_parallel_seconds": round(controlled_parallel, 6),
        "controlled_io_speedup": _speedup(controlled_serial, controlled_parallel),
        "parallel_groups": outcome.stats.parallel_groups,
        "parallel_calls": outcome.stats.parallel_calls,
        "serial_calls": outcome.stats.serial_calls,
    }


def _subagents(root: Path, profile: str) -> dict[str, object]:
    root.mkdir()
    (root / "project.txt").write_text("bounded project", encoding="utf-8")
    delay = 0.01 if profile == "smoke" else 0.04
    created: list[FakeModelClient] = []
    lock = threading.Lock()

    def create() -> FakeModelClient:
        client = FakeModelClient([ModelTurn("finding")], delay_seconds=delay)
        with lock:
            created.append(client)
        return client

    serial_manager = SubagentManager(root, create)
    serial_tasks = tuple(
        SubagentTask(f"serial-{index}", f"investigate {index}", SubagentRole.EXPLORE, _fresh_mode())
        for index in range(3)
    )
    serial_results, serial_elapsed = timed(
        lambda: tuple(serial_manager.run_child(task) for task in serial_tasks)
    )

    parallel_manager = SubagentManager(root, create)
    large_parent = "parent-history-evidence " * 1_000
    parallel_manager.observe_parent_context((Message(Role.USER, large_parent),))
    requests = tuple(SubagentRequest(f"investigate {index}") for index in range(3))
    parallel_results, parallel_elapsed = timed(lambda: parallel_manager.delegate(requests))
    if any(result.status is not RunStatus.FINAL_RESPONSE for result in serial_results + parallel_results):
        raise AssertionError("Subagent investigation did not complete")
    parallel_clients = created[-3:]
    max_child_context_chars = max(
        sum(len(message.content or "") for message in client.calls[0][0])
        for client in parallel_clients
    )
    if max_child_context_chars >= len(large_parent):
        raise AssertionError("fresh Subagent inherited full parent history")
    return {
        "serial_seconds": round(serial_elapsed, 6),
        "parallel_seconds": round(parallel_elapsed, 6),
        "speedup": _speedup(serial_elapsed, parallel_elapsed),
        "child_steps": sum(result.steps for result in parallel_results),
        "child_model_calls": sum(len(client.calls) for client in parallel_clients),
        "parent_model_calls": 0,
        "result_chars": sum(len(result.result) for result in parallel_results),
        "parent_context_chars": len(large_parent),
        "max_child_context_chars": max_child_context_chars,
    }


def _fresh_mode():
    from coding_agent.subagents.models import SubagentContextMode

    return SubagentContextMode.FRESH


def _context_scaling(profile: str) -> dict[str, object]:
    sizes = (10, 50) if profile == "smoke" else (10, 50, 100, 500)
    observations: list[dict[str, object]] = []
    for turn_count in sizes:
        history = ConversationHistory("core rules", "original task")
        payload = "x" * 1_200
        for index in range(turn_count):
            call = ToolCall(
                f"read-{index}",
                "read_file",
                json.dumps({"path": "parser.py"}),
            )
            history.append(Message(Role.USER, f"continue turn {index}"))
            history.append(Message(Role.ASSISTANT, tool_calls=(call,)))
            history.append(
                Message(
                    Role.TOOL,
                    ToolResult(call.id, call.name, True, payload).as_message_content(),
                    tool_call_id=call.id,
                )
            )
        policy = ContextPolicy(
            max_context_chars=10_000,
            max_tool_output_chars=512,
            skill_chars=1_000,
            memory_chars=1_000,
            summary_chars=1_000,
            recall_chars=1_000,
            recent_turns=8,
            minimum_recent_turns=2,
            summary_trigger_chars=2_000,
        )
        manager = ContextManager(policy=policy)
        summary = (
            SummaryState("older activity summary", 1, datetime.now(timezone.utc))
            if turn_count >= 100
            else None
        )
        context, elapsed = timed(lambda: manager.build(history, summary=summary))
        report = manager.last_report
        canonical_chars = sum(
            len(message.content or "")
            + sum(len(call.arguments_json) for call in message.tool_calls)
            for message in history.messages
        )
        levels = [
            level
            for level, active in (
                ("L1", report.tool_results_truncated > 0),
                ("L2", report.stale_results_pruned > 0),
                ("L3", report.activity_compressed_turns > 0),
                ("L4", report.summary_used),
            )
            if active
        ]
        observations.append(
            {
                "turns": turn_count,
                "canonical_history_chars": canonical_chars,
                "final_context_chars": report.final_context_chars,
                "context_messages": len(context),
                "build_seconds": round(elapsed, 6),
                "compression_levels": levels,
                "stale_results_pruned": report.stale_results_pruned,
                "tool_results_truncated": report.tool_results_truncated,
                "activity_compressed_turns": report.activity_compressed_turns,
                "summary_used": report.summary_used,
                "turns_dropped": report.turns_dropped,
            }
        )
    if any(item["final_context_chars"] > 10_000 for item in observations):
        raise AssertionError("Context exceeded the configured budget")
    return {"sizes": observations}


def _speedup(serial_seconds: float, parallel_seconds: float) -> float | None:
    if parallel_seconds <= 0:
        return None
    return round(serial_seconds / parallel_seconds, 3)


def _session_scaling(root: Path, profile: str) -> dict[str, object]:
    sizes = (10,) if profile == "smoke" else (10, 100, 1_000)
    observations: list[dict[str, object]] = []
    for count in sizes:
        base = root / f"session-{count}"
        store, workspace, identifiers = _populate_sessions(base, count)
        store, startup_seconds = timed(lambda: JsonSessionStore(base / "home"))

        latest, latest_seconds = timed(lambda: store.load_latest(workspace))
        if latest is None or not store.last_report.latest_fast_path_used:
            raise AssertionError("latest Session did not use the pointer fast path")
        latest_fast_path = store.last_report.latest_fast_path_used

        listed, list_seconds = timed(
            lambda: store.list_sessions(workspace, limit=min(50, count))
        )
        files_loaded_for_list = store.last_report.session_files_loaded
        if not listed or files_loaded_for_list != 0:
            raise AssertionError("Session catalog list loaded canonical histories")

        index = SessionIndex(store.root, workspace)
        contains, lookup_seconds = timed(lambda: index.contains(identifiers[count // 2]))
        if not contains:
            raise AssertionError("Session metadata lookup missed a known id")

        target = store.load_session(identifiers[0], workspace)
        renamed, rename_seconds = timed(
            lambda: store.rename_session(target, "benchmark-renamed", make_latest=False)
        )
        if renamed.name != "benchmark-renamed":
            raise AssertionError("Session rename was not persisted")
        _next, delete_seconds = timed(
            lambda: store.delete_session(identifiers[0], workspace)
        )

        _hits, search_seconds = timed(
            lambda: store.search_session_results(workspace, "unicode", limit=5)
        )
        search_backend = store.last_report.search_backend
        recall = RecallService(store)
        recall_hits, recall_seconds = timed(
            lambda: recall.search(workspace, "unicode", limit=5)
        )
        observations.append(
            {
                "sessions": count,
                "startup_seconds": round(startup_seconds, 6),
                "latest_resume_seconds": round(latest_seconds, 6),
                "latest_fast_path": latest_fast_path,
                "list_seconds": round(list_seconds, 6),
                "listed": len(listed),
                "session_files_loaded_for_list": files_loaded_for_list,
                "metadata_lookup_seconds": round(lookup_seconds, 6),
                "rename_seconds": round(rename_seconds, 6),
                "delete_seconds": round(delete_seconds, 6),
                "search_seconds": round(search_seconds, 6),
                "recall_seconds": round(recall_seconds, 6),
                "recall_hits": len(recall_hits),
                "search_backend": search_backend,
                "index_bytes": index.database_path.stat().st_size,
            }
        )
    return {"sizes": observations}


def _populate_sessions(
    base: Path, count: int
) -> tuple[JsonSessionStore, Path, tuple[str, ...]]:
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    home = base / "home"
    identifiers = tuple(f"{index + 1:012x}" for index in range(count))
    ids = iter(identifiers)
    tick = 0
    lock = threading.Lock()
    origin = datetime(2026, 8, 30, tzinfo=timezone.utc)

    def clock() -> datetime:
        nonlocal tick
        with lock:
            current = origin + timedelta(microseconds=tick)
            tick += 1
            return current

    store = JsonSessionStore(home, clock=clock, id_generator=ids.__next__)
    for index in range(count):
        record = store.create_session(workspace, "fake", "fake-model")
        messages = (
            Message(Role.USER, f"investigate unicode parser case {index}"),
            Message(Role.ASSISTANT, f"unicode evidence result {index}"),
        )
        store.save(replace(record, messages=messages))
    return store, workspace.resolve(), identifiers


def _search_fts(root: Path, profile: str) -> dict[str, object]:
    count = 10 if profile == "smoke" else 1_000
    base = root / f"session-{count}"
    store = JsonSessionStore(base / "home")
    workspace = (base / "workspace").resolve()
    if not workspace.exists():
        store, workspace, _identifiers = _populate_sessions(base, count)
    canonical_files = tuple((store.root / "sessions").glob("*.json"))
    canonical_bytes = sum(path.stat().st_size for path in canonical_files)

    scan_results, scan_seconds = timed(
        lambda: store.search_session_results(
            workspace, "unicode", limit=10, fts_enabled=False
        )
    )
    scan_files_loaded = store.last_report.session_files_loaded
    fts_results, fts_seconds = timed(
        lambda: store.search_session_results(
            workspace, "unicode", limit=10, fts_enabled=True
        )
    )
    backend = store.last_report.search_backend
    fts_files_loaded = store.last_report.session_files_loaded
    if not scan_results or not fts_results or any(
        "unicode" not in item.snippet.casefold()
        for item in scan_results + fts_results
    ):
        raise AssertionError("Session search returned a non-matching result")
    index = SessionIndex(store.root, workspace)
    index.mark_stale()
    _listed, rebuild_seconds = timed(lambda: store.list_sessions(workspace, limit=10))
    if not store.last_report.index_rebuilt:
        raise AssertionError("stale Session index was not rebuilt")
    return {
        "sessions": count,
        "search_backend": backend,
        "scan_seconds": round(scan_seconds, 6),
        "fts_seconds": round(fts_seconds, 6),
        "speedup": _speedup(scan_seconds, fts_seconds),
        "scan_session_files_loaded": scan_files_loaded,
        "fts_session_files_loaded": fts_files_loaded,
        "result_materialization_count": len(fts_results),
        "rebuild_seconds": round(rebuild_seconds, 6),
        "database_bytes": index.database_path.stat().st_size,
        "canonical_history_bytes": canonical_bytes,
    }


def _memory_summary(root: Path) -> dict[str, object]:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    home = root / "home"
    memory_ids = iter(("00000001", "00000002", "00000003"))
    memory_store = WorkspaceMemoryStore(home, id_generator=memory_ids.__next__)
    memory_store.add(
        workspace,
        "python -m pytest -q",
        (),
        kind="command",
        key="test.command",
    )
    memory_store.add(
        workspace,
        "do not modify vendor/",
        (),
        kind="constraint",
        key="constraint.vendor",
    )
    memories, lookup_seconds = timed(
        lambda: memory_store.context_items_for_context(workspace)
    )
    context_manager = ContextManager()
    context_manager.set_workspace_memories(memories)
    history = ConversationHistory("core", "run project tests")
    history.append(Message(Role.USER, "continue"))
    history.append(Message(Role.ASSISTANT, "ok"))
    _context, retrieval_seconds = timed(lambda: context_manager.build(history))

    summary_history = ConversationHistory("core", "long task")
    for index in range(12):
        summary_history.append(Message(Role.USER, f"old user {index}"))
        summary_history.append(Message(Role.ASSISTANT, f"old assistant {index}"))
    summary_model = FakeModelClient(
        [ModelTurn("first bounded summary"), ModelTurn("incremental bounded summary")]
    )
    summary_manager = SummaryManager(
        summary_model, threshold_chars=1, recent_turns=2, max_summary_chars=1_000
    )
    first, first_seconds = timed(lambda: summary_manager.prepare(summary_history))
    if first is None:
        raise AssertionError("initial Summary was not generated")
    for index in range(12, 16):
        summary_history.append(Message(Role.USER, f"new user {index}"))
        summary_history.append(Message(Role.ASSISTANT, f"new assistant {index}"))
    second, incremental_seconds = timed(
        lambda: summary_manager.prepare(summary_history, first)
    )
    if second is None or second.covered_message_count <= first.covered_message_count:
        raise AssertionError("Summary did not advance incrementally")

    store = JsonSessionStore(
        home / "sessions-home", id_generator=iter(("abcdef000001",)).__next__
    )
    record = store.create_session(workspace, "fake", "fake-model")
    store.save(
        replace(
            record,
            messages=summary_history.persisted_messages,
            summary=second,
        )
    )
    restored, restart_seconds = timed(lambda: store.load_latest(workspace))
    if restored is None or restored.summary != second:
        raise AssertionError("Persistent Summary did not survive restart")
    return {
        "workspace_memory_items": len(memories),
        "memory_lookup_seconds": round(lookup_seconds, 6),
        "relevant_retrieval_seconds": round(retrieval_seconds, 6),
        "initial_summary_seconds": round(first_seconds, 6),
        "incremental_summary_seconds": round(incremental_seconds, 6),
        "summary_model_calls": len(summary_model.calls),
        "total_old_messages": second.covered_message_count,
        "incremental_new_messages": (
            second.covered_message_count - first.covered_message_count
        ),
        "restart_resume_seconds": round(restart_seconds, 6),
    }


def _tui_product(root: Path) -> dict[str, object]:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    config = RuntimeConfig(
        workspace.resolve(),
        "https://example.test/v1",
        "fake",
        "offline-benchmark-secret",
        "BENCHMARK_KEY",
        "disabled",
        frozenset({"BENCHMARK_KEY"}),
        12,
        30_000,
        4,
        2_000,
        10,
    )
    clients: list[FakeModelClient] = []

    def factory(
        _base_url: str, _model: str, _api_key: str, _thinking_mode: str
    ) -> FakeModelClient:
        client = FakeModelClient([ModelTurn("benchmark response")])
        clients.append(client)
        return client

    service = CodingAgentService.create(config, "fake", root / "home", factory)
    result = service.submit_task("render one coding task")
    if result.status is not RunStatus.FINAL_RESPONSE:
        raise AssertionError("product task did not complete")

    async def scenario() -> dict[str, object]:
        app = CodingAgentApp(service)
        started = time.perf_counter()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            interactive_seconds = time.perf_counter() - started
            conversation = app.query_one("#conversation", ConversationPane)
            activity = app.query_one("#activity", ActivityPane)
            burst_started = time.perf_counter()
            now = datetime.now(timezone.utc)
            for index in range(40):
                app.apply_product_event(
                    ProductEvent(
                        ProductEventKind.TOOL_FINISHED,
                        now,
                        service.snapshot().status.session_id,
                        "benchmark-task",
                        index,
                        "read_file",
                        detail=f"bounded activity {index}",
                        status=ActivityStatus.SUCCEEDED,
                        source=ActivitySource.BUILTIN_TOOL,
                        tool_name="read_file",
                        tool_call_id=f"benchmark-{index}",
                    )
                )
            await pilot.pause()
            burst_seconds = time.perf_counter() - burst_started
            burst_activity_chars = len(activity.plain_text)
            original = service.snapshot().status.session_id
            switch_started = time.perf_counter()
            created = service.new_session()
            service.switch_session(original)
            app._refresh_all(service.snapshot())
            await pilot.pause()
            switch_seconds = time.perf_counter() - switch_started
            resize_started = time.perf_counter()
            app._apply_responsive(70)
            app._apply_responsive(140)
            await pilot.pause()
            resize_seconds = time.perf_counter() - resize_started
            app.action_command_palette()
            await pilot.pause()
            await pilot.press("escape")
            return {
                "cold_start_to_interactive_seconds": round(interactive_seconds, 6),
                "activity_burst_seconds": round(burst_seconds, 6),
                "session_switch_seconds": round(switch_seconds, 6),
                "resize_seconds": round(resize_seconds, 6),
                "conversation_items": len(service.get_conversation()),
                "activity_chars": burst_activity_chars,
                "created_session": bool(created.session_id),
                "conversation_rendered": "benchmark response" in conversation.plain_text,
                "model_calls": {
                    "parent": result.steps,
                    "skill_selector": 0,
                    "summary": 0,
                    "memory_candidate": 0,
                    "subagent": 0,
                    "total": len(clients[0].calls),
                },
                "no_crash": True,
            }

    return asyncio.run(scenario())

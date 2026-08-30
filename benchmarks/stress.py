"""Bounded repeated-operation stress scenario."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.plugins import PluginManager
from coding_agent.protocol import Message, ModelTurn, Role, RunStatus, ToolCall, ToolDefinition, ToolResult
from coding_agent.session_store import JsonSessionStore
from coding_agent.subagents.manager import SubagentManager
from coding_agent.subagents.models import SubagentRequest
from coding_agent.tool_execution import ToolExecutionScheduler
from coding_agent.tools.registry import RegisteredTool, ToolEffect, ToolRegistry

from .fakes import FakeModelClient


def run_bounded_stress(root: Path) -> dict[str, object]:
    root.mkdir()
    tool_calls = _many_tool_calls()
    switches, searches = _repeated_sessions(root / "sessions")
    cancelled = _repeated_cancellation()
    batches, children = _repeated_subagents(root / "subagents")
    plugin_cycles = _repeated_plugins(root / "plugins")
    return {
        "tool_calls": tool_calls,
        "session_switches": switches,
        "session_searches": searches,
        "cancelled_runs": cancelled,
        "subagent_batches": batches,
        "subagents_completed": children,
        "plugin_enable_disable_cycles": plugin_cycles,
    }


def _many_tool_calls() -> int:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            ToolDefinition(
                "stress_read",
                "Return a bounded read observation.",
                {"type": "object", "properties": {}},
            ),
            lambda arguments: arguments,
            lambda call_id, _arguments: ToolResult(
                call_id, "stress_read", True, "ok"
            ),
            effect=ToolEffect.READ_ONLY,
            parallel_safe=True,
        )
    )
    calls = tuple(
        ToolCall(f"stress-{index}", "stress_read", "{}") for index in range(50)
    )
    outcome = ToolExecutionScheduler(registry).execute(calls)
    if outcome.cancelled or len(outcome.results) != 50 or not all(
        item.ok for item in outcome.results
    ):
        raise AssertionError("bounded Tool stress did not complete")
    return len(outcome.results)


def _repeated_sessions(root: Path) -> tuple[int, int]:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    identifiers = tuple(f"{index + 1:012x}" for index in range(30))
    store = JsonSessionStore(root / "home", id_generator=iter(identifiers).__next__)
    for index in range(30):
        record = store.create_session(workspace, "fake", "fake-model")
        store.save(
            replace(
                record,
                messages=(
                    Message(Role.USER, f"stress unicode {index}"),
                    Message(Role.ASSISTANT, "bounded result"),
                ),
            )
        )
    for index in range(40):
        store.load_session(identifiers[index % len(identifiers)], workspace)
    for _index in range(20):
        results = store.search_session_results(workspace, "unicode", limit=1)
        if len(results) != 1:
            raise AssertionError("bounded repeated search failed")
    return 40, 20


def _repeated_cancellation() -> int:
    runner = AgentRunner(FakeModelClient([]), ToolRegistry(), ContextManager())
    completed = 0
    for index in range(50):
        result = runner.run_turn(
            ConversationHistory("system"),
            f"cancel {index}",
            cancel_check=lambda: True,
        )
        if result.status is not RunStatus.CANCELLED:
            raise AssertionError("pre-call cancellation was not honored")
        completed += 1
    return completed


def _repeated_subagents(root: Path) -> tuple[int, int]:
    root.mkdir()
    manager = SubagentManager(
        root, lambda: FakeModelClient([ModelTurn("bounded finding")])
    )
    requests = tuple(SubagentRequest(f"inspect area {index}") for index in range(3))
    completed = 0
    for _index in range(10):
        manager.begin_parent_run()
        results = manager.delegate(requests)
        if any(result.status is not RunStatus.FINAL_RESPONSE for result in results):
            raise AssertionError("bounded Subagent batch failed")
        completed += len(results)
    return 10, completed


def _repeated_plugins(root: Path) -> int:
    workspace = root / "workspace"
    package = root / "home" / "plugins" / "stress-plugin"
    workspace.mkdir(parents=True)
    package.mkdir(parents=True)
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "name": "stress-plugin",
                "version": "1.0.0",
                "description": "Bounded benchmark plugin.",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (package / "plugin.py").write_text(
        "from coding_agent.protocol import ToolDefinition, ToolResult\n"
        "from coding_agent.tools.registry import RegisteredTool, ToolEffect\n\n"
        "def get_tools(_context):\n"
        "    return (RegisteredTool(\n"
        "        ToolDefinition('stress_plugin_probe', 'Bounded probe.', "
        "{'type': 'object', 'properties': {}}),\n"
        "        lambda arguments: arguments,\n"
        "        lambda call_id, _arguments: ToolResult("
        "call_id, 'stress_plugin_probe', True, 'ok'),\n"
        "        effect=ToolEffect.READ_ONLY, parallel_safe=True,\n"
        "    ),)\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    manager = PluginManager(root / "home", workspace, registry)
    if len(manager.discover()) != 1:
        raise AssertionError("stress Plugin was not discovered")
    for _index in range(30):
        manager.enable("stress-plugin", persist=False)
        if registry.source_of("stress_plugin_probe") != "plugin:stress-plugin":
            raise AssertionError("stress Plugin tool was not registered")
        manager.disable("stress-plugin", persist=False)
        if registry.source_of("stress_plugin_probe") is not None:
            raise AssertionError("stress Plugin tool was not unregistered")
    manager.close()
    return 30

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.config import RuntimeConfig
from coding_agent.context import ContextManager
from coding_agent.plugins import PluginManager
from coding_agent.protocol import Message, ModelTurn, Role, ToolCall
from coding_agent.scheduler import BackgroundRuntime, BackgroundScheduler, JobStatus
from coding_agent.session_store import JsonSessionStore
from coding_agent.tools import build_default_registry
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)


def _install(home: Path, name: str = "demo", *, broken: bool = False) -> None:
    package = home / "plugins" / name
    package.mkdir(parents=True)
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "description": "Background demo",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    source = (
        "raise RuntimeError('private broken detail')"
        if broken
        else '''
from coding_agent.protocol import ToolDefinition, ToolResult
from coding_agent.tools.registry import RegisteredTool, require_keys
def get_tools(context):
    def validate(arguments):
        require_keys(arguments, required={})
        return arguments
    def handle(call_id, arguments):
        return ToolResult(call_id, "plugin_echo", True, "background plugin ok")
    return (RegisteredTool(ToolDefinition("plugin_echo", "Background plugin.", {"type":"object","properties":{},"additionalProperties":False}), validate, handle),)
'''
    )
    (package / "plugin.py").write_text(source, encoding="utf-8")


def _config(workspace: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=workspace.resolve(),
        base_url="https://example.test/v1",
        model="fake",
        api_key="fake",
        api_key_env="FAKE_KEY",
        thinking_mode="provider-default",
        sensitive_env_names=frozenset({"FAKE_KEY"}),
        max_steps=4,
        max_context_chars=4_000,
        recent_turns=2,
        max_tool_output_chars=500,
        command_timeout=1,
    )


def _record(store: JsonSessionStore, workspace: Path):
    return store.save(
        replace(
            store.create_session(workspace, "fake", "model"),
            messages=(Message(Role.USER, "original"), Message(Role.ASSISTANT, "ready")),
        )
    )


def test_scheduler_passes_submit_time_plugin_snapshot_to_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        tmp_path / "home", clock=lambda: NOW, id_generator=lambda: "111111111111"
    )
    record = _record(store, workspace)
    snapshots: list[tuple[str, ...]] = []

    def runtime(names: tuple[str, ...]) -> BackgroundRuntime:
        snapshots.append(names)
        return BackgroundRuntime(
            AgentRunner(
                FakeModelClient([ModelTurn("done")]),
                ToolRegistry(),
                ContextManager(),
            ),
            lambda: None,
        )

    scheduler = BackgroundScheduler(
        store, runtime, id_generator=lambda: "aaaaaaaa", max_workers=1
    )
    try:
        job = scheduler.submit(
            record,
            "task",
            (),
            enabled_plugin_names=("zeta", "alpha"),
        )
        completed = scheduler.wait(job.id, timeout=2)
    finally:
        scheduler.shutdown()

    assert completed.status is JobStatus.COMPLETED
    assert snapshots == [("zeta", "alpha")]


def test_queued_worker_uses_isolated_snapshot_after_foreground_disable(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home)
    foreground = PluginManager(home, workspace, ToolRegistry())
    foreground.enable("demo")
    store = JsonSessionStore(home, clock=lambda: NOW, id_generator=lambda: "111111111111")
    record = _record(store, workspace)
    entered = threading.Event()
    release = threading.Event()
    worker_definitions: list[tuple[str, ...]] = []
    worker_model = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("p1", "plugin_echo", "{}"),)),
            ModelTurn("worker done"),
        ]
    )

    def runtime(names: tuple[str, ...]) -> BackgroundRuntime:
        entered.set()
        release.wait(2)
        registry = build_default_registry(_config(workspace))
        worker_plugins = PluginManager(home, workspace, registry)
        worker_plugins.load_snapshot(names)
        worker_definitions.append(tuple(item.name for item in registry.definitions()))
        return BackgroundRuntime(
            AgentRunner(worker_model, registry, ContextManager()), lambda: None
        )

    scheduler = BackgroundScheduler(
        store, runtime, id_generator=lambda: "aaaaaaaa", max_workers=1
    )
    try:
        job = scheduler.submit(
            record,
            "background",
            (),
            enabled_plugin_names=foreground.enabled_names,
        )
        assert entered.wait(1)
        foreground.disable("demo")
        release.set()
        completed = scheduler.wait(job.id, timeout=3)
    finally:
        release.set()
        scheduler.shutdown()

    assert completed.status is JobStatus.COMPLETED
    assert "plugin_echo" in worker_definitions[0]
    assert foreground.registry.source_of("plugin_echo") is None
    assert any(
        message.role is Role.TOOL and "background plugin ok" in (message.content or "")
        for message in store.load_session(record.session_id, workspace).messages
    )


def test_snapshot_loading_does_not_rewrite_state_and_broken_plugin_keeps_builtins(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install(home, "broken", broken=True)
    state = home / "plugins.json"
    state.write_text(
        json.dumps({"schema_version": 1, "enabled": []}), encoding="utf-8"
    )
    before = state.read_bytes()
    registry = build_default_registry(_config(workspace))
    manager = PluginManager(home, workspace, registry)

    assert manager.load_snapshot(("broken", "missing")) == ()

    assert state.read_bytes() == before
    assert len(registry.definitions()) == 6
    assert {item.code for item in manager.diagnostics} >= {
        "PLUGIN_IMPORT_FAILED",
        "PLUGIN_ENABLED_MISSING",
    }
    assert "private broken detail" not in " ".join(
        item.message for item in manager.diagnostics
    )

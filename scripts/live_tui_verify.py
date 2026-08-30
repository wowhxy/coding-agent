"""Verification-only real-provider TUI smoke; never part of runtime credentials."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from coding_agent.application.events import ProductEventKind
from coding_agent.application.service import CodingAgentService
from coding_agent.config import resolve_config
from coding_agent.providers.openai_compatible import OpenAICompatibleClient
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, ConversationPane


_FALLBACK_NAME = "coding-agent-deepseek-live.key"


def _credential_path() -> Path:
    configured = os.environ.get("CODING_AGENT_LIVE_SECRET_FILE")
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
    if os.name == "nt":
        fallback = Path(tempfile.gettempdir()) / _FALLBACK_NAME
        if fallback.is_file():
            return fallback
    raise RuntimeError("live verification credential file is unavailable")


def _load_credential_for_verification() -> None:
    value = _credential_path().read_text(encoding="utf-8-sig").strip()
    if not value:
        raise RuntimeError("live verification credential file is empty")
    os.environ["DEEPSEEK_API_KEY"] = value


def _write_fixture(workspace: Path) -> None:
    (workspace / "duration.py").write_text(
        "def clamp_percentage(value: int) -> int:\n"
        "    return min(100, value)\n",
        encoding="utf-8",
    )
    (workspace / "test_duration.py").write_text(
        "from duration import clamp_percentage\n\n"
        "def test_inside_range():\n"
        "    assert clamp_percentage(42) == 42\n\n"
        "def test_above_range():\n"
        "    assert clamp_percentage(125) == 100\n\n"
        "def test_below_range():\n"
        "    assert clamp_percentage(-5) == 0\n",
        encoding="utf-8",
    )


async def _wait_until_idle(app: CodingAgentApp, pilot, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while app._running_task and time.monotonic() < deadline:
        await pilot.pause(0.1)
    if app._running_task:
        app.service.cancel_task()
        raise RuntimeError("live TUI task exceeded its bounded timeout")
    await pilot.pause()


async def _drive_tui(service: CodingAgentService, task: str) -> tuple[str, int]:
    app = CodingAgentApp(service)
    async with app.run_test(size=(110, 32)) as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = task
        await pilot.press("ctrl+enter")
        await _wait_until_idle(app, pilot, 240)
        text = app.query_one("#conversation", ConversationPane).plain_text
        active = next(item for item in service.snapshot().sessions if item.active)
        status = active.result_status or "UNKNOWN"
        app.exit()
        return text, 1 if composer.display else 0


def _task(case: str) -> str:
    base = (
        "Inspect this small Python project, run python -m pytest -q, fix the "
        "failing lower-bound bug with the smallest production-code change, do "
        "not modify tests, rerun the same test command, and finish only after "
        "the tests pass."
    )
    if case == "subagent":
        return (
            "First use delegate_tasks exactly once with three parallel read-only "
            "subagents using explore, analysis, and review roles to inspect the "
            "implementation, tests, and minimal repair. Then let the parent "
            "perform the edit and verification. " + base
        )
    return base


def _max_steps(case: str) -> int:
    """Keep the simple smoke tight and parallel orchestration at production default."""

    return 20 if case == "subagent" else 12


def _verification_subprocess_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    return {
        name: value
        for name, value in environment.items()
        if name != "DEEPSEEK_API_KEY"
    }


def run_case(case: str) -> int:
    _load_credential_for_verification()
    with tempfile.TemporaryDirectory(prefix=f"coding-agent-live-tui-{case}-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        _write_fixture(workspace)
        config = resolve_config(
            workspace=workspace,
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            thinking_mode="disabled",
            max_steps=_max_steps(case),
            max_context_chars=40_000,
            recent_turns=4,
            max_tool_output_chars=4_000,
            command_timeout=30,
        )
        service = CodingAgentService.create(
            config,
            "deepseek",
            root / "session-home",
            OpenAICompatibleClient,
            new_session=True,
        )
        events = []
        service.subscribe(events.append)
        try:
            rendered, composer_visible = asyncio.run(_drive_tui(service, _task(case)))
            snapshot = service.snapshot()
        finally:
            service.close()

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            env=_verification_subprocess_environment(os.environ),
            timeout=60,
            check=False,
        )
        active = next(item for item in snapshot.sessions if item.active)
        subagents = sum(
            event.kind is ProductEventKind.SUBAGENT_STARTED for event in events
        )
        checks = {
            "protocol_final": active.result_status == "FINAL_RESPONSE",
            "production_file_changed": any(
                item.path == "duration.py" for item in snapshot.changes
            ),
            "structured_verification": bool(snapshot.verifications)
            and snapshot.verifications[-1].ok,
            "independent_pytest": result.returncode == 0,
            "final_visible_in_tui": "passed" in rendered.casefold(),
            "composer_visible": composer_visible == 1,
            "native_tool_calling": any(
                event.kind is ProductEventKind.TOOL_STARTED for event in events
            ),
            "three_subagents": case != "subagent" or subagents == 3,
        }
        print(f"live_case={case}")
        print(f"protocol_status={active.result_status or 'UNKNOWN'}")
        for name, ok in checks.items():
            print(f"{name}={'yes' if ok else 'no'}")
        print(f"subagents_started={subagents}")
        return 0 if all(checks.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("coding", "subagent"), required=True)
    arguments = parser.parse_args()
    try:
        return run_case(arguments.case)
    except Exception as exc:
        print(f"live_verification_error={type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

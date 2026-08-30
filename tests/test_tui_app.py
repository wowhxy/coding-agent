from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Static

from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, ConversationPane, ProductStatusBar
from coding_agent.application.events import ActivityStatus, ProductEvent, ProductEventKind
from tests.tui_fakes import FakeProductService


def test_tui_mounts_mature_first_run_shell_and_focuses_composer(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            empty = app.query_one("#empty-state", Static)
            assert "Type a coding task" in str(empty.render())
            assert str(tmp_path) in str(empty.render())
            assert app.query_one("#conversation", ConversationPane)
            assert app.query_one("#status-bar", ProductStatusBar)
            assert app.focused is app.query_one("#composer", Composer)

    asyncio.run(scenario())


def test_product_events_posted_from_a_thread_update_ui_safely(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "fix parser"
            await pilot.press("ctrl+enter")
            await pilot.pause(0.1)
            assert service.tasks == ["fix parser"]
            assert "Done successfully" in app.query_one("#conversation", ConversationPane).plain_text
            assert app.focused is composer

    asyncio.run(scenario())


def test_quit_closes_service_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
        assert service.close_count == 1

    asyncio.run(scenario())


def test_submitted_user_task_is_visible_while_agent_is_still_running(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path, blocking=True)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "investigate the parser"
            await pilot.press("ctrl+enter")
            assert await asyncio.to_thread(service.started.wait, 2)
            await pilot.pause()
            assert "investigate the parser" in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            service.cancel_task()
            await pilot.pause(0.1)

    asyncio.run(scenario())


def test_provider_waiting_and_compact_context_status_are_visible(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            service.publish(
                ProductEvent(
                    ProductEventKind.MODEL_WAITING,
                    datetime.now(timezone.utc),
                    "111111111111",
                    "task-1",
                    None,
                    "Waiting for provider",
                    status=ActivityStatus.RUNNING,
                )
            )
            await pilot.pause()
            assert "Waiting for provider" in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            status = str(app.query_one("#status-bar", ProductStatusBar).render())
            assert f"ws {tmp_path.name}" in status
            assert "summary off" in status

    asyncio.run(scenario())

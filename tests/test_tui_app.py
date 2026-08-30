from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Static

from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import (
    ActivityPane,
    Composer,
    ConversationPane,
    ProductStatusBar,
)
from coding_agent.application.events import ActivityStatus, ProductEvent, ProductEventKind
from tests.tui_fakes import FakeProductService


def test_tui_mounts_mature_first_run_shell_and_focuses_composer(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            empty = app.query_one("#empty-state", Static)
            assert "type / to discover commands" in str(empty.render())
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
                "#activity", ActivityPane
            ).plain_text
            assert "Waiting for provider" not in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            status = str(app.query_one("#status-bar", ProductStatusBar).render())
            assert f"ws {tmp_path.name}" not in status
            assert "session 111111" not in status
            assert "sum off" in status
            assert "Waiting for provider" in status

    asyncio.run(scenario())


def test_running_phase_follows_structured_events_without_fake_percentage(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            events = (
                (ProductEventKind.TASK_STARTED, "task", "Working"),
                (ProductEventKind.MODEL_WAITING, "Waiting for provider", "Waiting for provider"),
                (ProductEventKind.TOOL_STARTED, "read_file", "Running tool"),
                (ProductEventKind.SUBAGENT_BATCH, "3", "Parallel investigation"),
                (ProductEventKind.VERIFICATION, "3 passed", "Verifying"),
            )
            for kind, title, expected in events:
                service.publish(
                    ProductEvent(
                        kind,
                        datetime.now(timezone.utc),
                        "111111111111",
                        "task-1",
                        1,
                        title,
                        status=ActivityStatus.RUNNING,
                    )
                )
                await pilot.pause()
                status = str(app.query_one("#status-bar", ProductStatusBar).render())
                assert expected in status
                assert "% complete" not in status

            service.publish(
                ProductEvent(
                    ProductEventKind.FINAL_RESPONSE,
                    datetime.now(timezone.utc),
                    "111111111111",
                    "task-1",
                    2,
                    "done",
                    status=ActivityStatus.SUCCEEDED,
                )
            )
            await pilot.pause()
            assert "Ready" in str(
                app.query_one("#status-bar", ProductStatusBar).render()
            )
            service.publish(
                ProductEvent(
                    ProductEventKind.ERROR,
                    datetime.now(timezone.utc),
                    "111111111111",
                    None,
                    None,
                    "Session Error",
                    status=ActivityStatus.FAILED,
                )
            )
            service.publish(
                ProductEvent(
                    ProductEventKind.SESSION_CHANGED,
                    datetime.now(timezone.utc),
                    "222222222222",
                    None,
                    None,
                    "Session switched",
                    status=ActivityStatus.SUCCEEDED,
                )
            )
            await pilot.pause()
            assert "Ready" in str(
                app.query_one("#status-bar", ProductStatusBar).render()
            )

    asyncio.run(scenario())

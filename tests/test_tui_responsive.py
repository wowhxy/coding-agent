from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import ActivityPane, Composer, ConversationPane
from coding_agent.application.events import ActivityStatus, ProductEvent, ProductEventKind
from coding_agent.application.state import ConversationItem, ConversationKind
from tests.tui_fakes import FakeProductService


def _conversation(prefix: str, count: int) -> tuple[ConversationItem, ...]:
    return tuple(
        ConversationItem(
            f"{prefix}-{index}",
            ConversationKind.USER if index % 2 == 0 else ConversationKind.ASSISTANT,
            f"{prefix} conversation item {index}",
        )
        for index in range(count)
    )


async def _settle(app: CodingAgentApp, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause(0.1)


def test_80x24_hides_sidebar_but_keeps_conversation_and_composer_visible(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.has_class("compact")
            assert app.query_one("#session-sidebar").display is False
            assert app.query_one("#conversation", ConversationPane).region.height > 0
            assert app.query_one("#activity", ActivityPane).region.height > 0
            assert app.query_one("#composer", Composer).region.height > 0

    asyncio.run(scenario())


def test_large_terminal_shows_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            assert not app.has_class("compact")
            assert app.query_one("#session-sidebar").display is True

    asyncio.run(scenario())


def test_large_persisted_conversation_mounts_without_losing_small_terminal_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._conversation = _conversation("bounded", 200)
        app = CodingAgentApp(service)
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(app, pilot)
            pane = app.query_one("#conversation", ConversationPane)
            assert "bounded conversation item 199" in pane.plain_text
            assert pane.max_scroll_y > 0
            assert pane.is_vertical_scroll_end
            assert app.query_one("#composer", Composer).region.height > 0

    asyncio.run(scenario())


def test_switching_sessions_waits_for_render_then_scrolls_new_history_to_bottom(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._conversation = _conversation("session-a", 120)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(app, pilot)
            pane = app.query_one("#conversation", ConversationPane)
            pane.scroll_home(animate=False, force=True)
            await pilot.pause(0.05)
            assert not pane.is_vertical_scroll_end

            service._conversation = _conversation("session-b", 160)
            app.handle_session_selected("222222222222")
            await _settle(app, pilot)

            assert "session-b conversation item 159" in pane.plain_text
            assert pane.is_vertical_scroll_end

    asyncio.run(scenario())


@pytest.mark.parametrize("items", [(), _conversation("single", 2)])
def test_opening_empty_or_single_turn_session_has_valid_bottom_position(
    tmp_path: Path,
    items: tuple[ConversationItem, ...],
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._conversation = items
        app = CodingAgentApp(service)
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(app, pilot)
            assert app.query_one(
                "#conversation", ConversationPane
            ).is_vertical_scroll_end

    asyncio.run(scenario())


def test_conversation_autofollows_new_output_only_when_user_is_at_bottom(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._conversation = _conversation("history", 100)
        app = CodingAgentApp(service)
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(app, pilot)
            pane = app.query_one("#conversation", ConversationPane)
            assert pane.is_vertical_scroll_end

            service.publish(
                ProductEvent(
                    ProductEventKind.TEXT_DELTA,
                    datetime.now(timezone.utc),
                    "111111111111",
                    "task-1",
                    None,
                    "\n".join(f"stream {index}" for index in range(40)),
                    status=ActivityStatus.RUNNING,
                )
            )
            await pilot.pause(0.05)
            assert pane.is_vertical_scroll_end

            pane.scroll_home(animate=False, force=True)
            await pilot.pause(0.05)
            position = pane.scroll_y
            service.publish(
                ProductEvent(
                    ProductEventKind.TEXT_DELTA,
                    datetime.now(timezone.utc),
                    "111111111111",
                    "task-1",
                    None,
                    "\nmore output" * 20,
                    status=ActivityStatus.RUNNING,
                )
            )
            await pilot.pause(0.05)
            assert pane.scroll_y == position

    asyncio.run(scenario())


def test_keyboard_resizes_both_side_panes_with_bounded_widths(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(150, 36)) as pilot:
            await pilot.pause()
            sessions = app.query_one("#session-sidebar")
            activity = app.query_one("#activity", ActivityPane)
            assert sessions.region.width == 30
            assert activity.region.width == 42

            await pilot.press("alt+right")
            await pilot.press("alt+shift+left")
            await pilot.pause()

            assert sessions.region.width == 34
            assert activity.region.width == 46

            for _ in range(20):
                await pilot.press("alt+left", "alt+shift+right")
            await pilot.pause()
            assert sessions.region.width == 24
            assert activity.region.width == 28

    asyncio.run(scenario())


def test_resize_hides_panes_without_losing_preferences_or_conversation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._conversation = _conversation("preserved", 12)
        app = CodingAgentApp(service)
        async with app.run_test(size=(150, 36)) as pilot:
            await pilot.press("alt+right", "alt+shift+left")
            await pilot.pause()
            sessions = app.query_one("#session-sidebar")
            activity = app.query_one("#activity", ActivityPane)
            pane = app.query_one("#conversation", ConversationPane)

            await pilot.resize_terminal(64, 24)
            await pilot.pause()
            assert sessions.display is False
            assert activity.display is False
            assert "preserved conversation item 11" in pane.plain_text

            await pilot.resize_terminal(150, 36)
            await pilot.pause()
            assert sessions.display is True
            assert activity.display is True
            assert sessions.region.width == 34
            assert activity.region.width == 46
            assert "preserved conversation item 11" in pane.plain_text

    asyncio.run(scenario())


def test_manual_toggle_preference_survives_responsive_round_trip(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(140, 36)) as pilot:
            sidebar = app.query_one("#session-sidebar")
            await pilot.press("ctrl+b")
            assert sidebar.display is False

            await pilot.resize_terminal(80, 24)
            await pilot.resize_terminal(140, 36)
            await pilot.pause()
            assert sidebar.display is False

    asyncio.run(scenario())

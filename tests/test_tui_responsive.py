from __future__ import annotations

import asyncio
from pathlib import Path

from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, ConversationPane
from coding_agent.application.state import ConversationItem, ConversationKind
from tests.tui_fakes import FakeProductService


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
        service._conversation = tuple(
            ConversationItem(
                f"item-{index}",
                ConversationKind.USER if index % 2 == 0 else ConversationKind.ASSISTANT,
                f"bounded conversation item {index}",
            )
            for index in range(200)
        )
        app = CodingAgentApp(service)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            pane = app.query_one("#conversation", ConversationPane)
            assert "bounded conversation item 199" in pane.plain_text
            assert app.query_one("#composer", Composer).region.height > 0

    asyncio.run(scenario())

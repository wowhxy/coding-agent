from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Input, OptionList

from coding_agent.tui.app import CodingAgentApp
from tests.tui_fakes import FakeProductService


def test_session_sidebar_filters_switches_and_creates(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            session_list = app.query_one("#session-list", OptionList)
            assert session_list.option_count == 2
            filter_input = app.query_one("#session-filter", Input)
            filter_input.value = "Tests"
            await pilot.pause()
            assert session_list.option_count == 1
            filter_input.value = ""
            await pilot.pause()
            session_list.highlighted = 1
            session_list.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert service.switches == ["222222222222"]
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert service.new_count == 1

    asyncio.run(scenario())


def test_sidebar_toggle_is_keyboard_discoverable(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(120, 36)) as pilot:
            sidebar = app.query_one("#session-sidebar")
            assert sidebar.display is True
            await pilot.press("ctrl+b")
            assert sidebar.display is False
            await pilot.press("ctrl+b")
            assert sidebar.display is True

    asyncio.run(scenario())


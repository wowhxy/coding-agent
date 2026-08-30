from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from textual.widgets import Input, OptionList, Static

from coding_agent.tui.app import CodingAgentApp
from coding_agent.application.state import AgentState
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


def test_session_header_is_human_first_and_unnamed_session_is_untitled(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            header = str(app.query_one("#product-header", Static).render())
            assert "Parser fix" in header
            assert "111111" in header
            assert "deepseek-v4-flash" not in header
            await pilot.press("ctrl+n")
            await pilot.pause()
            session_list = app.query_one("#session-list", OptionList)
            labels = [
                str(session_list.get_option_at_index(index).prompt)
                for index in range(session_list.option_count)
            ]
            assert any("Untitled" in label and "333333" in label for label in labels)
            assert all("Session 333333" not in label for label in labels)

    asyncio.run(scenario())


def test_session_results_use_product_labels_not_protocol_enum_names(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._sessions = (
            replace(service._sessions[0], result_status="FINAL_RESPONSE"),
            replace(service._sessions[1], result_status="MODEL_ERROR"),
        )
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            options = app.query_one("#session-list", OptionList)
            labels = "\n".join(
                str(options.get_option_at_index(index).prompt)
                for index in range(options.option_count)
            )
            assert "completed" in labels
            assert "error" in labels
            assert "final_response" not in labels.casefold()
            assert "model_error" not in labels.casefold()

    asyncio.run(scenario())


def test_running_session_hides_stale_previous_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._sessions = (
            replace(service._sessions[0], result_status="FINAL_RESPONSE"),
            service._sessions[1],
        )
        service._state = AgentState.RUNNING
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            options = app.query_one("#session-list", OptionList)
            active = str(options.get_option_at_index(0).prompt)
            assert "working" in active
            assert "completed" not in active

    asyncio.run(scenario())

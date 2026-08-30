from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Input, OptionList, Static

from coding_agent.application.state import AgentState
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, SessionContextMenu
from tests.tui_fakes import FakeProductService


def test_right_click_targets_session_without_switching_and_escape_closes_menu(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#session-list", offset=(2, 3), button=3)
            await pilot.pause()

            menu = app.query_one("#session-context-menu", OptionList)
            assert menu.display is True
            assert menu.target_session_id == "222222222222"
            assert service.switches == []
            assert "Parser fix" in str(
                app.query_one("#product-header", Static).render()
            )

            await pilot.press("escape")
            assert menu.display is False

    asyncio.run(scenario())


def test_context_menu_only_opens_from_session_item_and_outside_click_closes_it(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#conversation", button=3)
            menu = app.query_one("#session-context-menu", OptionList)
            assert menu.display is False

            await pilot.click("#session-list", offset=(2, 1), button=3)
            assert menu.display is True
            await pilot.click("#conversation")
            assert menu.display is False

    asyncio.run(scenario())


def test_context_rename_updates_target_but_preserves_active_conversation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#session-list", offset=(2, 3), button=3)
            menu = app.query_one("#session-context-menu", OptionList)
            menu.highlighted = 0
            await pilot.press("enter")

            value = app.screen.query_one("#rename-session-value", Input)
            assert value.value == "Tests"
            value.value = "  Unicode 解析器  "
            await pilot.press("enter")
            await pilot.pause()

            assert service.rename_targets[-1] == (
                "222222222222",
                "Unicode 解析器",
            )
            assert service.snapshot().status.session_id == "111111111111"
            labels = "\n".join(
                str(app.query_one("#session-list", OptionList).get_option_at_index(i).prompt)
                for i in range(2)
            )
            assert "Unicode 解析器" in labels
            assert "Parser fix" in str(app.query_one("#product-header", Static).render())

    asyncio.run(scenario())


def test_rename_persistence_failure_keeps_name_and_session_active(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service.rename_error = RuntimeError("storage unavailable")
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            session_list = app.query_one("#session-list", OptionList)
            session_list.highlighted = 0
            session_list.focus()
            await pilot.press("f2")
            value = app.screen.query_one("#rename-session-value", Input)
            value.value = "new name"
            await pilot.press("enter")
            await pilot.pause()

            assert service.snapshot().status.session_id == "111111111111"
            assert service.list_sessions()[0].display_name == "Parser fix"
            assert "Parser fix" in str(app.query_one("#product-header", Static).render())

    asyncio.run(scenario())


def test_rename_rejects_blank_and_escape_cancels_with_sidebar_focus(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            session_list = app.query_one("#session-list", OptionList)
            session_list.highlighted = 0
            session_list.focus()
            await pilot.press("f2")
            value = app.screen.query_one("#rename-session-value", Input)
            value.value = "   "
            await pilot.press("enter")
            assert app.screen.query_one("#rename-session-error", Static).display is True
            assert service.renames == []

            await pilot.press("escape")
            assert app.focused is session_list
            assert service.renames == []

    asyncio.run(scenario())


def test_context_delete_names_target_can_cancel_then_delete_non_active(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            async def open_delete() -> None:
                await pilot.click("#session-list", offset=(2, 3), button=3)
                menu = app.query_one("#session-context-menu", OptionList)
                menu.highlighted = 1
                await pilot.press("enter")

            await open_delete()
            question = str(app.screen.query_one("#delete-session-message", Static).render())
            assert "Tests" in question
            assert "persisted conversation history" in question
            assert "Workspace memory will not be deleted" in question
            await pilot.press("escape")
            assert len(service.list_sessions()) == 2
            assert app.focused is app.query_one("#session-list", OptionList)

            await open_delete()
            await pilot.click("#delete-session-confirm")
            await pilot.pause()
            assert service.delete_targets[-1] == "222222222222"
            assert service.snapshot().status.session_id == "111111111111"
            assert len(service.list_sessions()) == 1

    asyncio.run(scenario())


def test_deleting_active_session_selects_remaining_session_and_refreshes_view(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            session_list = app.query_one("#session-list", OptionList)
            session_list.highlighted = 0
            session_list.focus()
            await pilot.press("delete")
            await pilot.click("#delete-session-confirm")
            await pilot.pause()

            assert service.delete_targets[-1] == "111111111111"
            assert service.snapshot().status.session_id == "222222222222"
            assert "Tests" in str(app.query_one("#product-header", Static).render())
            assert app.focused is app.query_one("#composer", Composer)

    asyncio.run(scenario())


def test_delete_key_only_acts_when_session_list_is_focused(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "abc"
            composer.cursor_location = (0, 1)
            composer.focus()
            await pilot.press("delete")
            assert composer.text == "ac"
            assert len(app.screen_stack) == 1

            session_list = app.query_one("#session-list", OptionList)
            session_list.highlighted = 1
            session_list.focus()
            await pilot.press("delete")
            assert app.screen.query_one("#delete-session-message", Static)

    asyncio.run(scenario())


def test_running_session_delete_is_disabled_and_service_failure_keeps_item(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._state = AgentState.RUNNING
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#session-list", offset=(2, 1), button=3)
            menu = app.query_one("#session-context-menu", OptionList)
            assert menu.get_option("delete").disabled is True

            await pilot.press("escape")
            service._state = AgentState.READY
            service.delete_error = RuntimeError("disk is read-only")
            await pilot.click("#session-list", offset=(2, 3), button=3)
            menu.highlighted = 1
            await pilot.press("enter")
            await pilot.click("#delete-session-confirm")
            await pilot.pause()
            assert {item.session_id for item in service.list_sessions()} == {
                "111111111111",
                "222222222222",
            }

    asyncio.run(scenario())


def test_visible_new_session_action_and_ctrl_n_focus_composer(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#new-session")
            assert service.new_count == 1
            assert app.focused is app.query_one("#composer", Composer)
            assert "333333" in str(app.query_one("#product-header", Static).render())

            await pilot.press("ctrl+n")
            assert service.new_count == 2
            assert app.focused is app.query_one("#composer", Composer)

    asyncio.run(scenario())


def test_context_menu_new_session_uses_same_new_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#session-list", offset=(2, 1), button=3)
            menu = app.query_one("#session-context-menu", OptionList)
            menu.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()

            assert service.new_count == 1
            assert app.focused is app.query_one("#composer", Composer)
            assert menu.display is False

    asyncio.run(scenario())


def test_context_menu_is_clamped_inside_80_by_24_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodingAgentApp(FakeProductService(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            menu = app.query_one("#session-context-menu", SessionContextMenu)
            menu.open_for(
                app.service.snapshot().sessions[1],
                screen_x=79,
                screen_y=23,
                screen_width=80,
                screen_height=24,
            )
            await pilot.pause()
            assert menu.display is True
            assert menu.region.right <= 80
            assert menu.region.bottom <= 24

    asyncio.run(scenario())

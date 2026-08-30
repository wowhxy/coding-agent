from __future__ import annotations

import asyncio
from pathlib import Path

from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, ConversationPane, SlashCommandSuggestions
from tests.tui_fakes import FakeProductService


def test_enter_is_newline_ctrl_enter_submits_and_empty_is_protected(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "first line"
            composer.cursor_location = composer.document.end
            await pilot.press("enter")
            assert composer.text == "first line\n"
            composer.text += "second line"
            await pilot.press("ctrl+enter")
            await pilot.pause(0.1)
            assert service.tasks == ["first line\nsecond line"]
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert len(service.tasks) == 1

    asyncio.run(scenario())


def test_up_down_at_editor_boundary_navigates_command_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "remember this"
            await pilot.press("ctrl+enter")
            await pilot.pause(0.1)
            composer.focus()
            await pilot.press("up")
            assert composer.text == "remember this"
            await pilot.press("down")
            assert composer.text == ""

    asyncio.run(scenario())


def test_ctrl_c_cancels_running_task_and_restores_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path, blocking=True)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "long task"
            await pilot.press("ctrl+enter")
            assert await asyncio.to_thread(service.started.wait, 2)
            await pilot.pause()
            assert composer.disabled is False
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert service.cancel_count == 1
            assert composer.disabled is False
            assert app.focused is composer

    asyncio.run(scenario())


def test_running_task_allows_manual_rename_without_clearing_live_conversation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path, blocking=True)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "first task"
            await pilot.press("ctrl+enter")
            assert await asyncio.to_thread(service.started.wait, 2)
            await pilot.pause()

            assert composer.disabled is False
            composer.text = "/rename Manual title"
            await pilot.press("ctrl+enter")
            await pilot.pause()

            assert service.renames == ["Manual title"]
            assert "first task" in app.query_one(
                "#conversation", ConversationPane
            ).plain_text
            service.release.set()
            await pilot.pause(0.1)

    asyncio.run(scenario())


def test_escape_returns_focus_and_ctrl_c_clears_idle_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            app.query_one("#session-list").focus()
            await pilot.press("escape")
            assert app.focused is composer
            composer.text = "discard"
            await pilot.press("ctrl+c")
            assert composer.text == ""

    asyncio.run(scenario())


def test_slash_suggestions_narrow_and_tab_accepts_without_submitting(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            suggestions = app.query_one("#slash-suggestions", SlashCommandSuggestions)
            composer.text = "/s"
            await pilot.pause()
            assert suggestions.display is True
            assert suggestions.values == (
                "/sessions", "/session ", "/skills", "/skill ",
            )
            await pilot.press("tab")
            assert composer.text == "/sessions"
            assert service.tasks == []
            composer.text = "/skill "
            await pilot.pause()
            assert suggestions.values == (
                "/skill use ", "/skill off ", "/skill clear",
            )
            await pilot.press("down", "tab")
            assert composer.text == "/skill off "
            await pilot.press("escape")
            assert suggestions.display is False
            composer.text = "ordinary task"
            await pilot.pause()
            assert suggestions.display is False

    asyncio.run(scenario())

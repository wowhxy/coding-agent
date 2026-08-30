from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Button, Markdown, Static

from coding_agent.application.state import MemoryCandidateView
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.screens import ConfirmScreen, HelpScreen, ManagementScreen
from coding_agent.tui.screens import (
    CommandPaletteScreen,
    PluginManagementScreen,
    SkillManagementScreen,
)
from coding_agent.tui.widgets import Composer
from tests.tui_fakes import FakeProductService


async def _command(app: CodingAgentApp, pilot, text: str) -> None:
    composer = app.query_one("#composer", Composer)
    composer.text = text
    await pilot.press("ctrl+enter")
    await pilot.pause()


def test_management_commands_call_facade_and_show_trust_warning(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await _command(app, pilot, "/memory add build.system = cmake")
            await _command(app, pilot, "/skill use tdd")
            await _command(app, pilot, "/plugin enable git-readonly")
            await _command(app, pilot, "/recall parser")
            assert service.memory_actions[-1] == ("add", "build.system = cmake")
            assert service.skill_actions[-1] == ("use", "tdd")
            assert service.plugin_actions[-1] == ("enable", "git-readonly")
            assert service.recall_queries == ["parser"]
            assert isinstance(app.screen, ManagementScreen)
            assert "parser failed earlier" in app.screen.query_one(Markdown).source
            app.pop_screen()
            await _command(app, pilot, "/plugins")
            assert "trusted local code" in str(
                app.screen.query_one("#resource-warning", Static).render()
            )

    asyncio.run(scenario())


def test_destructive_session_and_memory_clear_require_confirmation(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            await _command(app, pilot, "/delete")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("escape")
            assert service.delete_count == 0
            assert not isinstance(app.screen, ConfirmScreen)
            await _command(app, pilot, "/delete")
            await pilot.click("#confirm")
            assert service.delete_count == 1
            await _command(app, pilot, "/memory clear")
            await pilot.click("#confirm")
            assert service.memory_actions[-1] == ("clear", "")

    asyncio.run(scenario())


def test_rename_help_and_candidate_confirmation_are_direct(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            await _command(app, pilot, "/rename Unicode parser")
            assert service.renames == ["Unicode parser"]
            await _command(app, pilot, "/help")
            assert isinstance(app.screen, HelpScreen)
            assert "Ctrl+Enter" in app.screen.query_one(Markdown).source
            await pilot.press("escape")
            assert not isinstance(app.screen, HelpScreen)
            service._candidates = (
                MemoryCandidateView("candidate-1", "test.command", "pytest -q", "command", "observed", "save"),
            )
            await _command(app, pilot, "ordinary task")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#confirm")
            assert service.candidate_decisions == [("candidate-1", True)]

    asyncio.run(scenario())


def test_skills_panel_can_activate_and_deactivate_selected_skill(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await _command(app, pilot, "/skills")
            assert isinstance(app.screen, SkillManagementScreen)
            detail = app.screen.query_one("#resource-detail", Markdown)
            assert "Test first" in detail.source
            assert "inactive" in detail.source
            await pilot.click("#resource-primary")
            assert service.skill_actions[-1] == ("use", "tdd")
            assert "manual" in detail.source
            await pilot.click("#resource-secondary")
            assert service.skill_actions[-1] == ("off", "tdd")
            assert "inactive" in detail.source

    asyncio.run(scenario())


def test_plugins_panel_manages_trusted_plugin_and_refreshes_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await _command(app, pilot, "/plugins")
            assert isinstance(app.screen, PluginManagementScreen)
            detail = app.screen.query_one("#resource-detail", Markdown)
            assert "trusted local code" in str(
                app.screen.query_one("#resource-warning", Static).render()
            )
            assert "trusted local code" not in detail.source
            assert "disabled" in detail.source
            await pilot.click("#resource-primary")
            assert service.plugin_actions[-1] == ("enable", "git-readonly")
            assert "enabled" in detail.source
            await pilot.click("#resource-secondary")
            assert service.plugin_actions[-1] == ("disable", "git-readonly")
            assert "disabled" in detail.source

    asyncio.run(scenario())


def test_command_palette_is_keyboard_discoverable_and_reuses_app_actions(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("ctrl+p")
            assert isinstance(app.screen, CommandPaletteScreen)
            assert "New Session" in app.screen.plain_text
            assert "Skills" in app.screen.plain_text
            await pilot.press("enter")
            assert service.new_count == 1

            await pilot.press("ctrl+p")
            await pilot.press("down", "down", "enter")
            assert isinstance(app.screen, SkillManagementScreen)

    asyncio.run(scenario())

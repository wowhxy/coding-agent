from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.application.events import ActivityStatus, ProductEvent, ProductEventKind
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import Composer, ConversationPane
from tests.tui_fakes import FakeProductService


def test_errors_are_actionable_cards_without_tracebacks(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            service.publish(
                ProductEvent(
                    ProductEventKind.ERROR,
                    datetime(2026, 8, 30, tzinfo=timezone.utc),
                    "s",
                    "task",
                    None,
                    "Provider Error",
                    "DeepSeek request timed out. Retry or check provider configuration.",
                    ActivityStatus.FAILED,
                )
            )
            await pilot.pause()
            text = app.query_one("#conversation", ConversationPane).plain_text
            assert "Provider Error" in text
            assert "Retry" in text
            assert "Traceback" not in text

    asyncio.run(scenario())


def test_worker_failure_restores_input_and_shows_only_exception_type(tmp_path: Path) -> None:
    class FailingService(FakeProductService):
        def submit_task(self, _text: str):
            raise RuntimeError("private provider detail")

    async def scenario() -> None:
        service = FailingService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "fail safely"
            await pilot.press("ctrl+enter")
            await pilot.pause(0.1)
            text = app.query_one("#conversation", ConversationPane).plain_text
            assert composer.disabled is False
            assert "Internal Error: RuntimeError" in text
            assert "private provider detail" not in text

    asyncio.run(scenario())


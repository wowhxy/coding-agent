from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Markdown, OptionList, Static

from coding_agent.application.events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
)
from coding_agent.application.state import ActivityView, ChangeStatus, ChangeView, VerificationView
from coding_agent.tui.app import CodingAgentApp
from coding_agent.tui.widgets import ActivityPane, ConversationPane
from tests.tui_fakes import FakeProductService


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def test_snapshot_renders_compact_tools_changed_files_and_actual_verification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._activities = (
            ActivityView(
                "a1", "tool", "read_file", "src/parser.py\nsource",
                ActivityStatus.SUCCEEDED, 1, True,
                ActivitySource.BUILTIN_TOOL, "read_file",
            ),
        )
        service._changes = (
            ChangeView("src/parser.py", ChangeStatus.MODIFIED, 2, 1, "@@ diff detail"),
        )
        service._verifications = (
            VerificationView("pytest -q", True, "42 passed in 1.8s", "exit_code: 0"),
        )
        app = CodingAgentApp(service)
        async with app.run_test(size=(120, 36)) as pilot:
            conversation = app.query_one("#conversation", ConversationPane)
            activity = app.query_one("#activity", ActivityPane)
            assert "read_file" not in conversation.plain_text
            assert "pytest" not in conversation.plain_text
            assert "[tool] read_file" in activity.plain_text
            assert "src/parser.py" in activity.plain_text
            assert "[verify] pytest -q" in activity.plain_text
            assert "source" not in activity.plain_text
            app.query_one("#activity-list", OptionList).highlighted = 0
            activity.toggle_selected_detail()
            assert "source" in activity.plain_text

    asyncio.run(scenario())


def test_compact_tool_activity_keeps_target_but_hides_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._activities = (
            ActivityView(
                "a1",
                "tool",
                "read_file",
                "src/parser.py\nvery large source payload",
                ActivityStatus.SUCCEEDED,
                1,
                True,
            ),
        )
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            activity = app.query_one("#activity", ActivityPane)
            assert "src/parser.py" in activity.plain_text
            assert "very large source payload" not in activity.plain_text
            activity.toggle_selected_detail()
            assert "very large source payload" in activity.plain_text

    asyncio.run(scenario())


def test_live_tool_updates_in_place_and_subagents_form_a_visible_tree(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            service.publish(ProductEvent(ProductEventKind.TOOL_STARTED, NOW, "s", "task", 2, "execute_command", "pytest -q", ActivityStatus.RUNNING, source=ActivitySource.COMMAND_VERIFICATION, tool_name="execute_command"))
            service.publish(ProductEvent(ProductEventKind.TOOL_FINISHED, NOW, "s", "task", 2, "execute_command", "42 passed", ActivityStatus.SUCCEEDED, source=ActivitySource.COMMAND_VERIFICATION, tool_name="execute_command"))
            service.publish(ProductEvent(ProductEventKind.SUBAGENT_STARTED, NOW, "s", "task", None, "inspect tests", status=ActivityStatus.RUNNING, metadata=(("role", "explore"), ("subagent_id", "subagent-1"))))
            service.publish(ProductEvent(ProductEventKind.SUBAGENT_FINISHED, NOW, "s", "task", None, "inspection complete", status=ActivityStatus.SUCCEEDED, metadata=(("role", "explore"), ("subagent_id", "subagent-1"))))
            await pilot.pause()
            text = app.query_one("#activity", ActivityPane).plain_text
            assert text.count("execute_command") == 1
            assert "succeeded" in text
            assert text.count("subagent-1") == 1
            assert "inspection complete" in text
            assert "execute_command" not in app.query_one(
                "#conversation", ConversationPane
            ).plain_text

    asyncio.run(scenario())


def test_builtin_and_plugin_tools_have_distinct_visible_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._activities = (
            ActivityView(
                "a1", "tool", "read_file", "parser.py",
                ActivityStatus.SUCCEEDED, 1, True,
                ActivitySource.BUILTIN_TOOL, "read_file",
            ),
            ActivityView(
                "a2", "tool", "git_diff", "--stat",
                ActivityStatus.SUCCEEDED, 2, True,
                ActivitySource.PLUGIN_TOOL, "git_diff", "git-readonly",
            ),
        )
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = app.query_one("#activity", ActivityPane).plain_text
            assert "[tool] read_file" in text
            assert "[plugin:git-readonly] git_diff" in text

    asyncio.run(scenario())


def test_snapshot_replaces_live_tool_rows_without_duplicates(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            service.publish(
                ProductEvent(
                    ProductEventKind.TOOL_STARTED, NOW, "s", "task", 1,
                    "read_file", status=ActivityStatus.RUNNING,
                    source=ActivitySource.BUILTIN_TOOL, tool_name="read_file",
                )
            )
            service.publish(
                ProductEvent(
                    ProductEventKind.TOOL_FINISHED, NOW, "s", "task", 1,
                    "read_file: ok", status=ActivityStatus.SUCCEEDED,
                    source=ActivitySource.BUILTIN_TOOL, tool_name="read_file",
                )
            )
            await pilot.pause()
            service._activities = (
                ActivityView(
                    "read-1", "tool", "read_file", "parser.py\nsource",
                    ActivityStatus.SUCCEEDED, 1, True,
                    ActivitySource.BUILTIN_TOOL, "read_file",
                ),
            )
            app._refresh_all(service.snapshot())
            await pilot.pause()

            assert app.query_one("#activity", ActivityPane).plain_text.count(
                "[tool] read_file"
            ) == 1

    asyncio.run(scenario())


def test_activity_autofollows_only_while_user_is_at_the_bottom(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        service._activities = tuple(
            ActivityView(
                f"a{index}", "tool", "read_file", f"file-{index}.py",
                ActivityStatus.SUCCEEDED, index, True,
                ActivitySource.BUILTIN_TOOL, "read_file",
            )
            for index in range(40)
        )
        app = CodingAgentApp(service)
        async with app.run_test(size=(100, 24)) as pilot:
            options = app.query_one("#activity-list", OptionList)
            await pilot.pause()
            assert options.is_vertical_scroll_end
            options.focus()
            await pilot.press("home")
            await pilot.pause()
            assert not options.is_vertical_scroll_end
            position = options.scroll_y

            service._activities += (
                ActivityView(
                    "new-1", "tool", "read_file", "new.py",
                    ActivityStatus.SUCCEEDED, 41, True,
                    ActivitySource.BUILTIN_TOOL, "read_file",
                ),
            )
            app._refresh_all(service.snapshot())
            await pilot.pause()
            assert options.scroll_y == position

            await pilot.press("end")
            await pilot.pause()
            assert options.is_vertical_scroll_end
            service._activities += (
                ActivityView(
                    "new-2", "tool", "read_file", "newer.py",
                    ActivityStatus.SUCCEEDED, 42, True,
                    ActivitySource.BUILTIN_TOOL, "read_file",
                ),
            )
            app._refresh_all(service.snapshot())
            await pilot.pause()
            assert options.is_vertical_scroll_end

    asyncio.run(scenario())


def test_streaming_chunks_update_one_agent_block(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            service.publish(ProductEvent(ProductEventKind.TEXT_DELTA, NOW, "s", "task", None, "hello ", status=ActivityStatus.RUNNING))
            service.publish(ProductEvent(ProductEventKind.TEXT_DELTA, NOW, "s", "task", None, "world", status=ActivityStatus.RUNNING))
            await pilot.pause()
            pane = app.query_one("#conversation", ConversationPane)
            assert "hello world" in pane.plain_text
            assert pane.plain_text.count("hello") == 1

    asyncio.run(scenario())


def test_streaming_deltas_do_not_reparse_markdown_for_every_chunk(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = FakeProductService(tmp_path)
        app = CodingAgentApp(service)
        async with app.run_test() as pilot:
            markdown = app.query_one("#conversation-markdown", Markdown)
            original_update = markdown.update
            markdown_updates = 0

            def counted_update(content) -> None:
                nonlocal markdown_updates
                markdown_updates += 1
                original_update(content)

            markdown.update = counted_update  # type: ignore[method-assign]
            for _index in range(100):
                service.publish(
                    ProductEvent(
                        ProductEventKind.TEXT_DELTA,
                        NOW,
                        "s",
                        "task",
                        None,
                        "x",
                        status=ActivityStatus.RUNNING,
                    )
                )
            await pilot.pause()
            assert markdown_updates == 0
            stream = app.query_one("#streaming-text", Static)
            assert "x" * 100 in str(stream.render())

    asyncio.run(scenario())

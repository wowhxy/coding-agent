"""Focused Textual widgets for the coding-agent product shell."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..application.events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
)
from ..application.commands import command_suggestions
from ..application.state import ActivityView, ProductSnapshot, ProductStatus, SessionView


class Composer(TextArea):
    """Multiline editor with history navigation at document boundaries."""

    def __init__(self, *, id: str = "composer") -> None:
        super().__init__(
            id=id,
            placeholder="Describe a coding task...  Ctrl+Enter to submit",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
        )

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            action = getattr(self.app, "action_accept_suggestion", None)
            if callable(action) and action():
                event.prevent_default()
                event.stop()
                return
        if event.key in {"up", "down"}:
            action = getattr(self.app, "action_navigate_suggestion", None)
            direction = -1 if event.key == "up" else 1
            if callable(action) and action(direction):
                event.prevent_default()
                event.stop()
                return
        if event.key == "up" and self.cursor_location[0] == 0:
            action = getattr(self.app, "action_history_previous", None)
            if callable(action):
                action()
                event.prevent_default()
                event.stop()
                return
        if (
            event.key == "down"
            and self.cursor_location[0] == self.document.end[0]
        ):
            action = getattr(self.app, "action_history_next", None)
            if callable(action):
                action()
                event.prevent_default()
                event.stop()
                return
        await super()._on_key(event)


class SlashCommandSuggestions(OptionList):
    """Small deterministic completion list driven by the command grammar."""

    def __init__(self, *, id: str = "slash-suggestions") -> None:
        super().__init__(id=id, compact=True)
        self.values: tuple[str, ...] = ()

    def update_for(self, text: str) -> None:
        suggestions = command_suggestions(text)
        self.values = tuple(item.value for item in suggestions)
        self.set_options(
            Option(f"{item.value}  — {item.description}", id=str(index))
            for index, item in enumerate(suggestions)
        )
        self.highlighted = 0 if suggestions else None
        self.display = bool(suggestions)

    def accept_highlighted(self) -> str | None:
        if self.highlighted is None or self.highlighted >= len(self.values):
            return None
        return self.values[self.highlighted]

    def navigate(self, direction: int) -> bool:
        if not self.display or not self.values or direction not in {-1, 1}:
            return False
        current = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (current + direction) % len(self.values)
        return True

    def dismiss_suggestions(self) -> None:
        self.display = False


class SessionContextRequested(Message):
    """A native right-click selected a specific Session item."""

    def __init__(self, session_id: str, screen_x: int, screen_y: int) -> None:
        super().__init__()
        self.session_id = session_id
        self.screen_x = screen_x
        self.screen_y = screen_y


class SessionActionRequested(Message):
    """A Session widget requested an app-level management action."""

    def __init__(self, action: str, session_id: str | None) -> None:
        super().__init__()
        self.action = action
        self.session_id = session_id


class SessionOptionList(OptionList):
    """Session list with target-aware mouse and keyboard management."""

    BINDINGS = [
        Binding("f2", "rename_selected", "Rename", show=False),
        Binding("delete", "delete_selected", "Delete", show=False),
    ]

    async def _on_click(self, event: events.Click) -> None:
        if event.button == 3:
            clicked = event.style.meta.get("option")
            if clicked is not None:
                option = self.get_option_at_index(clicked)
                if not option.disabled and option.id is not None:
                    self.highlighted = clicked
                    self.post_message(
                        SessionContextRequested(
                            option.id,
                            event.screen_x if event.screen_x is not None else event.x,
                            event.screen_y if event.screen_y is not None else event.y,
                        )
                    )
                event.prevent_default()
                event.stop()
            return
        await super()._on_click(event)

    def action_rename_selected(self) -> None:
        session_id = self._highlighted_session_id()
        if session_id is not None:
            self.post_message(SessionActionRequested("rename", session_id))

    def action_delete_selected(self) -> None:
        session_id = self._highlighted_session_id()
        if session_id is not None:
            self.post_message(SessionActionRequested("delete", session_id))

    def _highlighted_session_id(self) -> str | None:
        option = self.highlighted_option
        return option.id if option is not None else None


class SessionContextMenu(OptionList):
    """Small non-modal menu positioned next to a right-clicked Session."""

    BINDINGS = [Binding("escape", "dismiss_menu", "Close", show=False)]
    MENU_WIDTH = 24
    MENU_HEIGHT = 7

    def __init__(self, *, id: str = "session-context-menu") -> None:
        super().__init__(id=id, compact=True)
        self.target_session_id: str | None = None

    def open_for(
        self,
        session: SessionView,
        *,
        screen_x: int,
        screen_y: int,
        screen_width: int,
        screen_height: int,
    ) -> None:
        self.target_session_id = session.session_id
        self.set_options(
            (
                Option("Rename", id="rename"),
                Option("Delete", id="delete", disabled=session.running),
                Option("────────────────────", id="separator", disabled=True),
                Option("New Session", id="new"),
            )
        )
        self.highlighted = 0
        x = min(max(0, screen_x), max(0, screen_width - self.MENU_WIDTH))
        y = min(max(0, screen_y), max(0, screen_height - self.MENU_HEIGHT))
        self.styles.offset = (x, y)
        self.display = True
        self.focus()

    def close_menu(self) -> None:
        self.display = False

    @on(OptionList.OptionSelected)
    def _action_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None or event.option_id == "separator":
            return
        target = self.target_session_id
        self.close_menu()
        self.post_message(
            SessionActionRequested(
                "new" if event.option_id == "new" else event.option_id,
                target,
            )
        )

    def action_dismiss_menu(self) -> None:
        self.close_menu()
        handler = getattr(self.app, "restore_session_list_focus", None)
        if callable(handler):
            handler()


class SessionSidebar(Vertical):
    """Filterable human-first session navigation."""

    def __init__(self, *, id: str = "session-sidebar") -> None:
        super().__init__(id=id)
        self._sessions: tuple[SessionView, ...] = ()

    def compose(self) -> ComposeResult:
        with Horizontal(id="sessions-header"):
            yield Static("Sessions", classes="panel-title")
            yield Button("+ New", id="new-session", compact=True)
        yield Input(placeholder="Filter sessions", id="session-filter")
        yield SessionOptionList(id="session-list", compact=True)
        yield Static("F2 rename  Del delete  Ctrl+N new", classes="sidebar-help")

    @on(Button.Pressed, "#new-session")
    def _new_session(self, _event: Button.Pressed) -> None:
        self.post_message(SessionActionRequested("new", None))

    def update_sessions(self, sessions: Iterable[SessionView]) -> None:
        self._sessions = tuple(sessions)
        self._apply_filter(self.query_one("#session-filter", Input).value)

    @on(Input.Changed, "#session-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(OptionList.OptionSelected, "#session-list")
    def _session_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        handler = getattr(self.app, "handle_session_selected", None)
        if callable(handler):
            handler(event.option_id)

    def _apply_filter(self, query: str) -> None:
        needle = query.strip().casefold()
        visible = tuple(
            item
            for item in self._sessions
            if not needle
            or needle in item.display_name.casefold()
            or needle in item.session_id.casefold()
        )
        options = [
            Option(
                _session_label(item),
                id=item.session_id,
            )
            for item in visible
        ]
        option_list = self.query_one("#session-list", SessionOptionList)
        option_list.set_options(options)
        active_index = next(
            (index for index, item in enumerate(visible) if item.active), None
        )
        option_list.highlighted = active_index


class ConversationPane(Vertical):
    """Scrollable canonical user/assistant conversation only."""

    can_focus = True

    def __init__(self, *, id: str = "conversation") -> None:
        super().__init__(id=id)
        self.plain_text = ""
        self._stream_text = ""
        self._base_plain = ""
        self._workspace = ""
        self._session_id = ""
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="empty-state")
        yield Markdown("", id="conversation-markdown")
        yield Static("", id="streaming-text", markup=False)

    def show_snapshot(self, snapshot: ProductSnapshot) -> None:
        session_changed = snapshot.status.session_id != self._session_id
        should_follow = session_changed or self._at_bottom()
        previous_scroll_y = self.scroll_y
        self._session_id = snapshot.status.session_id
        self._workspace = str(snapshot.status.workspace)
        blocks: list[str] = []
        plain: list[str] = []
        for item in snapshot.conversation:
            label = "You" if item.kind.value == "user" else "Agent"
            blocks.append(f"### {label}\n\n{item.content}")
            plain.append(item.content.replace("**", ""))
        self._stream_text = ""
        streaming = self.query_one("#streaming-text", Static)
        streaming.update("")
        streaming.display = False
        self._base_plain = "\n".join(plain)
        completion = self.query_one("#conversation-markdown", Markdown).update(
            "\n\n".join(blocks)
        )
        self._finish_render_after(
            completion,
            follow_bottom=should_follow,
            previous_scroll_y=previous_scroll_y,
        )
        self._sync_plain_text()
        empty = self.query_one("#empty-state", Static)
        empty.display = not blocks
        empty.update(
            "Start coding with your agent\n\n"
            f"Workspace: {self._workspace}\n\n"
            "Describe a task below, or type / to discover commands.\n"
            "Examples: Fix the failing tests | Explain this repository | Add parser tests"
        )

    def apply_event(self, event: ProductEvent) -> None:
        if event.kind is ProductEventKind.TASK_STARTED:
            markdown = self.query_one("#conversation-markdown", Markdown)
            separator = "\n\n" if markdown.source else ""
            completion = markdown.update(
                markdown.source + separator + "### You\n\n" + event.title
            )
            self._finish_render_after(
                completion,
                follow_bottom=True,
                previous_scroll_y=self.scroll_y,
            )
            self._base_plain = "\n".join(filter(None, (self._base_plain, event.title)))
            self._sync_plain_text()
            self.query_one("#empty-state", Static).display = False
        elif event.kind is ProductEventKind.TEXT_DELTA:
            should_follow = self._at_bottom()
            if should_follow:
                self.anchor()
            self._stream_text += event.title
            self._render_stream()

    def _sync_plain_text(self) -> None:
        stream = self._stream_text.replace("**", "")
        self.plain_text = "\n".join(
            part for part in (self._base_plain, stream) if part
        )

    def _render_stream(self) -> None:
        if not self._stream_text:
            return
        streaming = self.query_one("#streaming-text", Static)
        streaming.display = True
        streaming.update("Agent (streaming)\n\n" + self._stream_text)
        self._sync_plain_text()

    def _at_bottom(self) -> bool:
        return self.max_scroll_y <= 0 or self.is_vertical_scroll_end

    def _finish_render_after(
        self,
        completion: Awaitable[object],
        *,
        follow_bottom: bool,
        previous_scroll_y: float,
    ) -> None:
        self._render_generation += 1
        generation = self._render_generation

        async def finish() -> None:
            await completion
            if generation != self._render_generation:
                return
            positioned = asyncio.Event()

            def apply_position() -> None:
                try:
                    if generation != self._render_generation:
                        return
                    if follow_bottom:
                        # Anchoring keeps the pane at the real bottom if Markdown's
                        # final layout grows again after this refresh. User scrolling
                        # releases the anchor through Textual's normal scroll API.
                        self.anchor()
                        self.scroll_to(
                            y=max(0, self.max_scroll_y),
                            animate=False,
                            force=True,
                            immediate=True,
                            release_anchor=False,
                        )
                    else:
                        self.scroll_to(
                            y=previous_scroll_y,
                            animate=False,
                            force=True,
                            immediate=True,
                        )
                finally:
                    positioned.set()

            # Markdown.update() completes before Textual's next layout refresh.
            # Apply the final position in that refresh so scroll bounds are current,
            # without scroll_end() introducing another deferred refresh cycle.
            if self.call_after_refresh(apply_position):
                await positioned.wait()

        self.run_worker(
            finish,
            group="conversation-render",
            exclusive=True,
            exit_on_error=False,
        )


@dataclass(slots=True)
class _ActivityRecord:
    key: str
    source: ActivitySource
    title: str
    detail: str
    status: ActivityStatus
    step: int | None = None
    tool_name: str | None = None
    plugin_name: str | None = None
    parent_id: str | None = None
    live: bool = False


class ActivityPane(Vertical):
    """Independent operational timeline with selectable bounded details."""

    _MAX_RECORDS = 200

    def __init__(self, *, id: str = "activity") -> None:
        super().__init__(id=id)
        self.plain_text = ""
        self._records: list[_ActivityRecord] = []
        self._detail_open = False
        self._session_id = ""

    def compose(self) -> ComposeResult:
        yield Static("Activity", classes="panel-title")
        yield OptionList(id="activity-list", compact=True)
        yield Static("Select an item and press Enter for details", id="activity-detail")

    def show_snapshot(self, snapshot: ProductSnapshot) -> None:
        live = (
            [
                record
                for record in self._records
                if record.live
                and (
                    record.key.startswith("subagent:")
                    or record.key.endswith(":subagents")
                )
            ]
            if self._session_id == snapshot.status.session_id
            else []
        )
        self._session_id = snapshot.status.session_id
        records = [_record_from_activity(item) for item in snapshot.activities]
        records.extend(live)
        for change in snapshot.changes:
            marker = {"added": "A", "modified": "M", "deleted": "D"}[
                change.status.value
            ]
            records.append(
                _ActivityRecord(
                    f"change:{change.path}",
                    ActivitySource.TASK,
                    f"[change] {marker} {change.path} +{change.additions} -{change.deletions}",
                    change.diff,
                    ActivityStatus.SUCCEEDED,
                )
            )
        for index, verification in enumerate(snapshot.verifications):
            records.append(
                _ActivityRecord(
                    f"verification:{index}:{verification.command}",
                    ActivitySource.COMMAND_VERIFICATION,
                    f"[verify] {verification.command} · {verification.summary}",
                    verification.detail,
                    ActivityStatus.SUCCEEDED if verification.ok else ActivityStatus.FAILED,
                )
            )
        self._records = records[-self._MAX_RECORDS :]
        self._render_records()

    def apply_event(self, event: ProductEvent) -> None:
        if event.kind in {
            ProductEventKind.TEXT_DELTA,
            ProductEventKind.FINAL_RESPONSE,
            ProductEventKind.SESSION_CHANGED,
            ProductEventKind.MEMORY_CANDIDATE,
            ProductEventKind.RECALL_RESULT,
            ProductEventKind.NOTICE,
        }:
            return
        record, replace_key = _record_from_event(event, len(self._records))
        if record is None:
            return
        index = self._find_replace_index(replace_key, record)
        if index is None:
            self._records.append(record)
            self._records = self._records[-self._MAX_RECORDS :]
        else:
            record.key = self._records[index].key
            self._records[index] = record
        self._render_records()

    def toggle_selected_detail(self) -> None:
        option_list = self.query_one("#activity-list", OptionList)
        if option_list.highlighted is None or not self._records:
            return
        self._detail_open = not self._detail_open
        self._render_detail()

    @on(OptionList.OptionSelected, "#activity-list")
    def _activity_selected(self, _event: OptionList.OptionSelected) -> None:
        self.toggle_selected_detail()

    @on(OptionList.OptionHighlighted, "#activity-list")
    def _activity_highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._render_detail()

    def _find_replace_index(
        self, replace_key: str | None, record: _ActivityRecord
    ) -> int | None:
        if replace_key is not None:
            return next(
                (index for index, item in enumerate(self._records) if item.key == replace_key),
                None,
            )
        if record.tool_name is None or record.status is ActivityStatus.RUNNING:
            return None
        for index in range(len(self._records) - 1, -1, -1):
            item = self._records[index]
            if (
                item.tool_name == record.tool_name
                and item.step == record.step
                and item.status is ActivityStatus.RUNNING
            ):
                return index
        return None

    def _render_records(self) -> None:
        option_list = self.query_one("#activity-list", OptionList)
        selected_key = (
            self._records[option_list.highlighted].key
            if option_list.highlighted is not None
            and option_list.highlighted < len(self._records)
            else None
        )
        should_follow = not option_list.options or option_list.is_vertical_scroll_end
        option_list.set_options(
            Option(_activity_line(item), id=item.key) for item in self._records
        )
        selected_index = (
            len(self._records) - 1
            if should_follow and self._records
            else next(
                (
                    index
                    for index, item in enumerate(self._records)
                    if item.key == selected_key
                ),
                0 if self._records else None,
            )
        )
        option_list.highlighted = selected_index
        self._render_detail()
        if should_follow and self._records:
            self.call_after_refresh(option_list.scroll_end, animate=False)

    def _render_detail(self) -> None:
        option_list = self.query_one("#activity-list", OptionList)
        detail_widget = self.query_one("#activity-detail", Static)
        detail = ""
        if (
            self._detail_open
            and option_list.highlighted is not None
            and option_list.highlighted < len(self._records)
        ):
            detail = self._records[option_list.highlighted].detail
        detail_widget.display = bool(detail)
        detail_widget.update(detail)
        lines = [_activity_line(item) for item in self._records]
        if detail:
            lines.append(detail)
        self.plain_text = "\n".join(lines)


class ProductStatusBar(Static):
    """One compact line of product state rather than implementation payloads."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.plain_text = ""
        self._status: ProductStatus | None = None
        self._phase = "Ready"

    def update_status(self, status: ProductStatus, phase: str = "") -> None:
        self._status = status
        if phase:
            self._phase = phase
        compact = 0 < self.size.width < 96
        if compact:
            parts = [
                f"{status.provider}/{status.model}",
                f"ctx {status.context_percent}%",
                f"sum {'on' if status.summary_active else 'off'}",
                f"mem {status.memory_count}",
                f"sk {len(status.active_skills)}",
                f"pl {len(status.enabled_plugins)}",
            ]
        else:
            parts = [
                f"{status.provider}/{status.model}",
                f"ctx {status.context_percent}%",
                f"summary {'on' if status.summary_active else 'off'}",
                f"memory {status.memory_count}",
                f"skills {len(status.active_skills)}",
                f"plugins {len(status.enabled_plugins)}",
            ]
        if status.active_subagents:
            parts.append(f"subagents {status.active_subagents}")
        parts.append(self._phase)
        self.plain_text = "  ".join(parts)
        self.update(self.plain_text)

    def refresh_width(self) -> None:
        if self._status is not None:
            self.update_status(self._status, self._phase)


def _session_label(item: SessionView) -> str:
    active = "●" if item.active else " "
    running = " working" if item.running else ""
    result = ""
    if item.result_status and not item.running:
        result_label = (
            "completed"
            if item.result_status == "FINAL_RESPONSE"
            else "cancelled"
            if item.result_status == "CANCELLED"
            else "error"
        )
        result = f" {result_label}"
    return f"{active} {item.display_name}\n  {item.session_id[:6]}{running}{result}"


def _record_from_activity(item: ActivityView) -> _ActivityRecord:
    target, separator, remaining = item.detail.partition("\n")
    title = _source_title(item.source, item.tool_name or item.title, item.plugin_name)
    if target.strip():
        title += f" · {target.strip()}"
    return _ActivityRecord(
        f"snapshot:{item.id}",
        item.source,
        title,
        remaining if separator else "",
        item.status,
        item.step,
        item.tool_name,
        item.plugin_name,
        item.parent_id,
    )


def _record_from_event(
    event: ProductEvent,
    ordinal: int,
) -> tuple[_ActivityRecord | None, str | None]:
    source = event.source or _event_source(event.kind)
    status = event.status or ActivityStatus.RUNNING
    if event.kind in {ProductEventKind.TOOL_STARTED, ProductEventKind.TOOL_FINISHED}:
        name = event.tool_name or event.title
        title = _source_title(source, name, event.plugin_name)
        detail = event.detail
        if event.kind is ProductEventKind.TOOL_FINISHED and event.title != name:
            detail = "\n".join(part for part in (event.title, detail) if part)
        key = (
            f"tool:{event.tool_call_id}"
            if event.tool_call_id is not None
            else f"tool:{event.step}:{name}:{ordinal}"
        )
        return (
            _ActivityRecord(
                key,
                source,
                title,
                detail,
                status,
                event.step,
                name,
                event.plugin_name,
                event.parent_id,
                True,
            ),
            key
            if event.kind is ProductEventKind.TOOL_FINISHED
            and event.tool_call_id is not None
            else None,
        )
    if event.kind is ProductEventKind.SUBAGENT_BATCH:
        key = event.parent_id or f"{event.task_id}:subagents"
        return (
            _ActivityRecord(
                key,
                ActivitySource.CONTROL_SUBAGENT,
                f"[subagent] Parallel batch · {event.title}",
                event.detail,
                status,
                parent_id=key,
                live=True,
            ),
            key,
        )
    if event.kind in {
        ProductEventKind.SUBAGENT_STARTED,
        ProductEventKind.SUBAGENT_FINISHED,
    }:
        metadata = dict(event.metadata)
        child_id = metadata.get("subagent_id", "subagent")
        role = metadata.get("role", "worker")
        key = f"subagent:{child_id}"
        return (
            _ActivityRecord(
                key,
                ActivitySource.CONTROL_SUBAGENT,
                f"  [subagent] {child_id} ({role}) · {event.title}",
                event.detail,
                status,
                parent_id=event.parent_id,
                live=True,
            ),
            key,
        )
    if event.kind is ProductEventKind.VERIFICATION:
        command = event.detail.strip()
        label = f"[verify] {command}" if command else "[verify] Verification"
        if event.title:
            label += f" · {event.title}"
        return (
            _ActivityRecord(
                f"verify:{ordinal}", source, label, "", status, live=True
            ),
            None,
        )
    if event.kind is ProductEventKind.FILE_CHANGES:
        return (
            _ActivityRecord(
                f"changes:{ordinal}", source, f"[change] {event.title}", event.detail,
                status, live=True,
            ),
            None,
        )
    if event.kind is ProductEventKind.TASK_STARTED:
        return (
            _ActivityRecord(
                f"task:{event.task_id}", ActivitySource.TASK, "[state] Working",
                event.title, status, live=True,
            ),
            None,
        )
    if event.kind is ProductEventKind.MODEL_WAITING:
        return (
            _ActivityRecord(
                f"waiting:{ordinal}", ActivitySource.TASK,
                f"[state] {event.title}", event.detail, status, live=True,
            ),
            None,
        )
    if event.kind in {ProductEventKind.ERROR, ProductEventKind.TASK_FAILED}:
        summary = event.detail.splitlines()[0].strip() if event.detail else ""
        title = f"[error] {event.title}"
        if summary:
            title += f" · {summary[:160]}"
        return (
            _ActivityRecord(
                f"error:{ordinal}", ActivitySource.ERROR, title,
                event.detail, status, live=True,
            ),
            None,
        )
    if event.kind is ProductEventKind.TASK_CANCELLED:
        return (
            _ActivityRecord(
                f"cancelled:{ordinal}", ActivitySource.TASK,
                f"[state] {event.title}", event.detail, status, live=True,
            ),
            None,
        )
    return None, None


def _event_source(kind: ProductEventKind) -> ActivitySource:
    if kind in {ProductEventKind.ERROR, ProductEventKind.TASK_FAILED}:
        return ActivitySource.ERROR
    if kind is ProductEventKind.VERIFICATION:
        return ActivitySource.COMMAND_VERIFICATION
    if kind in {
        ProductEventKind.SUBAGENT_BATCH,
        ProductEventKind.SUBAGENT_STARTED,
        ProductEventKind.SUBAGENT_FINISHED,
    }:
        return ActivitySource.CONTROL_SUBAGENT
    return ActivitySource.TASK


def _source_title(
    source: ActivitySource,
    name: str,
    plugin_name: str | None,
) -> str:
    if source is ActivitySource.PLUGIN_TOOL:
        return f"[plugin:{plugin_name or 'unknown'}] {name}"
    if source is ActivitySource.CONTROL_SUBAGENT:
        return f"[subagent] {name}"
    if source is ActivitySource.COMMAND_VERIFICATION:
        return f"[command] {name}"
    if source is ActivitySource.ERROR:
        return f"[error] {name}"
    if source is ActivitySource.TASK:
        return f"[state] {name}"
    return f"[tool] {name}"


def _activity_line(record: _ActivityRecord) -> str:
    icon = {
        ActivityStatus.RUNNING: ">",
        ActivityStatus.QUEUED: ".",
        ActivityStatus.SUCCEEDED: "+",
        ActivityStatus.FAILED: "!",
        ActivityStatus.CANCELLED: "x",
    }[record.status]
    return f"{icon} {record.title} [{record.status.value}]"

"""Focused Textual widgets for the coding-agent product shell."""

from __future__ import annotations

from collections.abc import Iterable

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..application.events import ActivityStatus, ProductEvent, ProductEventKind
from ..application.state import ProductSnapshot, ProductStatus, SessionView


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


class SessionSidebar(Vertical):
    """Filterable human-first session navigation."""

    def __init__(self, *, id: str = "session-sidebar") -> None:
        super().__init__(id=id)
        self._sessions: tuple[SessionView, ...] = ()

    def compose(self) -> ComposeResult:
        yield Static("Sessions", classes="panel-title")
        yield Input(placeholder="Filter sessions", id="session-filter")
        yield OptionList(id="session-list", compact=True)
        yield Static("Ctrl+N new  /rename  /delete", classes="sidebar-help")

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
        option_list = self.query_one("#session-list", OptionList)
        option_list.set_options(options)
        active_index = next(
            (index for index, item in enumerate(visible) if item.active), None
        )
        option_list.highlighted = active_index


class ConversationPane(Vertical):
    """Scrollable Markdown conversation plus bounded observable activity."""

    can_focus = True

    def __init__(self, *, id: str = "conversation") -> None:
        super().__init__(id=id)
        self.plain_text = ""
        self._stream_text = ""
        self._base_plain = ""
        self._activity_records: list[dict[str, object]] = []
        self._expanded = False
        self._workspace = ""
        self._session_id = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="empty-state")
        yield Markdown("", id="conversation-markdown")
        yield Static("", id="streaming-text", markup=False)
        yield Static("", id="activity-panel")

    def show_snapshot(self, snapshot: ProductSnapshot) -> None:
        self._workspace = str(snapshot.status.workspace)
        preserved_subagents = (
            [
                record
                for record in self._activity_records
                if str(record["key"]).startswith("subagent:")
            ]
            if self._session_id == snapshot.status.session_id
            else []
        )
        self._session_id = snapshot.status.session_id
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
        self._activity_records = [
            {
                "key": f"snapshot:{item.id}",
                "title": _compact_activity_title(item.title, item.detail),
                "status": item.status,
                "detail": _remaining_activity_detail(item.detail),
                "always_detail": False,
            }
            for item in snapshot.activities
        ]
        self._activity_records.extend(preserved_subagents)
        for change in snapshot.changes:
            marker = {
                "added": "A",
                "modified": "M",
                "deleted": "D",
            }[change.status.value]
            self._activity_records.append(
                {
                    "key": f"change:{change.path}",
                    "title": (
                        f"{marker} {change.path} "
                        f"+{change.additions} -{change.deletions}"
                    ),
                    "status": ActivityStatus.SUCCEEDED,
                    "detail": change.diff,
                    "always_detail": False,
                }
            )
        for index, verification in enumerate(snapshot.verifications):
            self._activity_records.append(
                {
                    "key": f"verification:{index}:{verification.command}",
                    "title": f"{verification.command}: {verification.summary}",
                    "status": (
                        ActivityStatus.SUCCEEDED
                        if verification.ok
                        else ActivityStatus.FAILED
                    ),
                    "detail": verification.detail,
                    "always_detail": False,
                }
            )
        self.query_one("#conversation-markdown", Markdown).update("\n\n".join(blocks))
        self._render_activities()
        empty = self.query_one("#empty-state", Static)
        empty.display = not blocks and not self._activity_records
        empty.update(
            "Coding Agent\n\n"
            f"Workspace: {self._workspace}\n\n"
            "Type a coding task below to begin.\n"
            "Examples: Fix the failing tests | Explain this repository | Add parser tests"
        )

    def apply_event(self, event: ProductEvent) -> None:
        if event.kind is ProductEventKind.TASK_STARTED:
            markdown = self.query_one("#conversation-markdown", Markdown)
            separator = "\n\n" if markdown.source else ""
            markdown.update(markdown.source + separator + "### You\n\n" + event.title)
            self._base_plain = "\n".join(filter(None, (self._base_plain, event.title)))
            self._sync_plain_text()
            self.query_one("#empty-state", Static).display = False
        elif event.kind is ProductEventKind.TEXT_DELTA:
            self._stream_text += event.title
            self._render_stream()
        elif event.kind in {
            ProductEventKind.MODEL_WAITING,
            ProductEventKind.TOOL_STARTED,
            ProductEventKind.TOOL_FINISHED,
            ProductEventKind.SUBAGENT_BATCH,
            ProductEventKind.SUBAGENT_STARTED,
            ProductEventKind.SUBAGENT_FINISHED,
            ProductEventKind.VERIFICATION,
            ProductEventKind.FILE_CHANGES,
        }:
            self._upsert_event_activity(event)
        elif event.kind in {
            ProductEventKind.ERROR,
            ProductEventKind.TASK_FAILED,
            ProductEventKind.TASK_CANCELLED,
        }:
            self._activity_records.append(
                {
                    "key": f"error:{len(self._activity_records)}",
                    "title": event.title,
                    "status": event.status or ActivityStatus.FAILED,
                    "detail": event.detail,
                    "always_detail": True,
                }
            )
            self._render_activities()

    def toggle_activity_detail(self) -> None:
        self._expanded = not self._expanded

    def _upsert_event_activity(self, event: ProductEvent) -> None:
        metadata = dict(event.metadata)
        status = event.status or ActivityStatus.RUNNING
        match_index: int | None = None
        key: str
        title = event.title
        if event.kind in {ProductEventKind.TOOL_STARTED, ProductEventKind.TOOL_FINISHED}:
            key = f"tool:{event.step}:{len(self._activity_records)}"
            if event.kind is ProductEventKind.TOOL_FINISHED:
                match_index = self._latest_running_index("tool:", event.step)
        elif event.kind in {
            ProductEventKind.SUBAGENT_STARTED,
            ProductEventKind.SUBAGENT_FINISHED,
        }:
            child_id = metadata.get("subagent_id", "subagent")
            role = metadata.get("role", "worker")
            key = f"subagent:{child_id}"
            title = f"{child_id} ({role}): {event.title}"
            match_index = self._record_index(key)
        else:
            key = f"event:{event.kind.value}:{event.step}:{len(self._activity_records)}"
        record = {
            "key": key,
            "title": title,
            "status": status,
            "detail": event.detail,
            "always_detail": False,
            "step": event.step,
        }
        if match_index is None:
            self._activity_records.append(record)
        else:
            record["key"] = self._activity_records[match_index]["key"]
            self._activity_records[match_index] = record
        self._render_activities()

    def _latest_running_index(self, prefix: str, step: int | None) -> int | None:
        for index in range(len(self._activity_records) - 1, -1, -1):
            record = self._activity_records[index]
            if (
                str(record["key"]).startswith(prefix)
                and record.get("step") == step
                and record["status"] is ActivityStatus.RUNNING
            ):
                return index
        return None

    def _record_index(self, key: str) -> int | None:
        return next(
            (
                index
                for index, record in enumerate(self._activity_records)
                if record["key"] == key
            ),
            None,
        )

    def _render_activities(self) -> None:
        lines = [
            _activity_line(
                str(record["title"]),
                record["status"],
                str(record["detail"]),
                self._expanded or bool(record["always_detail"]),
            )
            for record in self._activity_records
        ]
        self.query_one("#activity-panel", Static).update("\n".join(lines))
        self._sync_plain_text(lines)

    def _sync_plain_text(self, activity_lines: list[str] | None = None) -> None:
        if activity_lines is None:
            activity_lines = [
                _activity_line(
                    str(record["title"]),
                    record["status"],
                    str(record["detail"]),
                    self._expanded or bool(record["always_detail"]),
                )
                for record in self._activity_records
            ]
        stream = self._stream_text.replace("**", "")
        self.plain_text = "\n".join(
            part for part in (self._base_plain, *activity_lines, stream) if part
        )

    def _render_stream(self) -> None:
        if not self._stream_text:
            return
        streaming = self.query_one("#streaming-text", Static)
        streaming.display = True
        streaming.update("Agent (streaming)\n\n" + self._stream_text)
        self._sync_plain_text()


class ProductStatusBar(Static):
    """One compact line of product state rather than implementation payloads."""

    def update_status(self, status: ProductStatus) -> None:
        self.update(
            f"{status.provider}/{status.model}  "
            f"ws {status.workspace.name}  "
            f"session {status.session_id[:6]}  "
            f"ctx {status.context_percent}%  "
            f"summary {'on' if status.summary_active else 'off'}  "
            f"mem {status.memory_count}  "
            f"skills {len(status.active_skills)}  "
            f"plugins {len(status.enabled_plugins)}  "
            f"subagents {status.active_subagents}  "
            f"{status.agent_state.value}"
        )


def _session_label(item: SessionView) -> str:
    active = "●" if item.active else " "
    running = " working" if item.running else ""
    result = f" {item.result_status.lower()}" if item.result_status else ""
    return f"{active} {item.display_name}\n  {item.session_id[:6]}{running}{result}"


def _activity_line(
    title: str,
    status: ActivityStatus,
    detail: str,
    expanded: bool,
) -> str:
    icon = {
        ActivityStatus.RUNNING: "◌",
        ActivityStatus.QUEUED: "·",
        ActivityStatus.SUCCEEDED: "✓",
        ActivityStatus.FAILED: "✗",
        ActivityStatus.CANCELLED: "■",
    }[status]
    line = f"{icon} {title} [{status.value}]"
    if expanded and detail:
        line += "\n  " + detail.replace("\n", "\n  ")
    return line


def _compact_activity_title(title: str, detail: str) -> str:
    first_line = detail.splitlines()[0].strip() if detail else ""
    return f"{title}\n  {first_line}" if first_line else title


def _remaining_activity_detail(detail: str) -> str:
    _first, separator, remaining = detail.partition("\n")
    return remaining if separator else ""

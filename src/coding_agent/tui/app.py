"""Responsive Textual application over the synchronous product facade."""

from __future__ import annotations

from typing import Any

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Footer, Static

from ..application.commands import (
    CommandAction,
    CommandError,
    CommandName,
    command_help,
    parse_command,
)
from ..application.events import ActivityStatus, ProductEvent, ProductEventKind
from ..application.service import CodingAgentService
from ..application.state import AgentState, ProductSnapshot
from .screens import ConfirmScreen, HelpScreen, ManagementScreen
from .widgets import Composer, ConversationPane, ProductStatusBar, SessionSidebar


class ProductEventMessage(Message):
    """Thread-safe Textual handoff for an immutable product event."""

    def __init__(self, event: ProductEvent) -> None:
        super().__init__()
        self.event = event


class TaskWorkerFinished(Message):
    pass


class CodingAgentApp(App[None]):
    """Full-screen product that never parses core stdout."""

    CSS_PATH = "theme.tcss"
    TITLE = "Coding Agent"
    BINDINGS = [
        Binding("ctrl+enter", "submit", "Submit", priority=True),
        Binding("ctrl+c", "cancel_or_clear", "Cancel/Clear", priority=True),
        Binding("escape", "focus_input", "Input"),
        Binding("ctrl+n", "new_session", "New session", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sessions", priority=True),
        Binding("ctrl+l", "toggle_activity", "Activity", priority=True),
        Binding("ctrl+k", "show_help", "Help", priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
    ]

    def __init__(self, service: CodingAgentService | Any) -> None:
        super().__init__()
        self.service = service
        self._unsubscribe = None
        self._closed_service = False
        self._running_task = False
        self._sidebar_requested = True
        self._input_history: list[str] = []
        self._history_index = 0
        self._last_transient_error: ProductEvent | None = None

    def compose(self) -> ComposeResult:
        yield Static("Coding Agent", id="product-header")
        with Horizontal(id="product-body"):
            yield SessionSidebar()
            with Vertical(id="main-panel"):
                yield ConversationPane()
                yield Composer()
        yield ProductStatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._unsubscribe = self.service.subscribe(
            lambda event: self.post_message(ProductEventMessage(event))
        )
        self._refresh_all(self.service.snapshot())
        self._apply_responsive(self.size.width)
        self.query_one("#composer", Composer).focus()

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._close_service_once()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive(event.size.width)

    @on(ProductEventMessage)
    def _product_event(self, message: ProductEventMessage) -> None:
        self.apply_product_event(message.event)

    @on(TaskWorkerFinished)
    def _task_finished(self, _message: TaskWorkerFinished) -> None:
        self._running_task = False
        composer = self.query_one("#composer", Composer)
        composer.disabled = False
        self._refresh_all(self.service.snapshot())
        if self._last_transient_error is not None:
            self.query_one("#conversation", ConversationPane).apply_event(
                self._last_transient_error
            )
            self._last_transient_error = None
        self._offer_pending_candidate()
        composer.focus()

    def action_submit(self) -> None:
        if self._running_task:
            self.notify("A task is already running", severity="warning")
            return
        composer = self.query_one("#composer", Composer)
        text = composer.text
        if not text.strip():
            self.notify("Enter a coding task first", severity="warning")
            return
        try:
            command = parse_command(text)
        except CommandError as exc:
            self.notify(exc.message, severity="error")
            return
        composer.clear()
        if command is not None:
            self._execute_command(command)
            composer.focus()
            return
        self._input_history.append(text)
        self._history_index = len(self._input_history)
        self._running_task = True
        composer.disabled = True
        self._run_task(text)

    @work(thread=True, exclusive=True, group="foreground-agent")
    def _run_task(self, text: str) -> None:
        try:
            self.service.submit_task(text)
        except Exception as exc:
            self.post_message(
                ProductEventMessage(
                    ProductEvent(
                        ProductEventKind.ERROR,
                        _utc_now(),
                        None,
                        None,
                        None,
                        f"Internal Error: {type(exc).__name__}",
                        status=ActivityStatus.FAILED,
                    )
                )
            )
        finally:
            self.post_message(TaskWorkerFinished())

    def action_cancel_or_clear(self) -> None:
        composer = self.query_one("#composer", Composer)
        if self._running_task:
            if self.service.cancel_task():
                self.notify("Cancellation requested")
            return
        composer.clear()
        composer.focus()

    def action_focus_input(self) -> None:
        self.query_one("#composer", Composer).focus()

    def action_new_session(self) -> None:
        if self._running_task:
            self.notify("Cancel the running task before switching sessions", severity="warning")
            return
        self.service.new_session()
        self._refresh_all(self.service.snapshot())
        self.query_one("#composer", Composer).focus()

    def action_toggle_sidebar(self) -> None:
        self._sidebar_requested = not self.query_one("#session-sidebar").display
        self.query_one("#session-sidebar").display = self._sidebar_requested

    def action_toggle_activity(self) -> None:
        self.query_one("#conversation", ConversationPane).toggle_activity_detail()
        self._refresh_all(self.service.snapshot())

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen(_help_markdown()))

    def action_request_quit(self) -> None:
        if self._running_task:
            self.push_screen(
                ConfirmScreen("Cancel the running task and quit?"),
                self._quit_confirmed,
            )
            return
        self.exit()

    def action_history_previous(self) -> None:
        if not self._input_history:
            return
        self._history_index = max(0, self._history_index - 1)
        self._set_composer_text(self._input_history[self._history_index])

    def action_history_next(self) -> None:
        if not self._input_history:
            return
        self._history_index = min(len(self._input_history), self._history_index + 1)
        value = (
            ""
            if self._history_index == len(self._input_history)
            else self._input_history[self._history_index]
        )
        self._set_composer_text(value)

    def handle_session_selected(self, session_id: str) -> None:
        if self._running_task:
            self.notify("Cancel the running task before switching sessions", severity="warning")
            return
        if session_id == self.service.snapshot().status.session_id:
            return
        self.service.switch_session(session_id)
        self._refresh_all(self.service.snapshot())
        self.query_one("#composer", Composer).focus()

    def apply_product_event(self, event: ProductEvent) -> None:
        pane = self.query_one("#conversation", ConversationPane)
        pane.apply_event(event)
        if event.kind is ProductEventKind.TASK_STARTED:
            self._last_transient_error = None
            self._running_task = True
        elif event.kind in {
            ProductEventKind.FINAL_RESPONSE,
            ProductEventKind.TASK_FAILED,
            ProductEventKind.TASK_CANCELLED,
        }:
            self._running_task = False
            self.query_one("#composer", Composer).disabled = False
            if event.kind in {
                ProductEventKind.TASK_FAILED,
                ProductEventKind.TASK_CANCELLED,
            }:
                self._last_transient_error = event
        elif event.kind is ProductEventKind.ERROR:
            self._last_transient_error = event
        status_getter = getattr(self.service, "get_status", None)
        status = (
            status_getter()
            if callable(status_getter)
            else self.service.snapshot().status
        )
        self.query_one("#status-bar", ProductStatusBar).update_status(status)

    def _execute_command(self, command) -> None:
        try:
            if command.name is CommandName.SESSION:
                self._execute_session_command(command)
            elif command.name is CommandName.MEMORY:
                self._execute_memory_command(command)
            elif command.name is CommandName.SKILL:
                self._execute_skill_command(command)
            elif command.name is CommandName.PLUGIN:
                self._execute_plugin_command(command)
            elif command.name is CommandName.RECALL:
                self._show_recall(command.argument)
            elif command.name is CommandName.HELP:
                self.action_show_help()
            self._refresh_all(self.service.snapshot())
        except Exception as exc:
            self.notify(
                f"{type(exc).__name__}: operation failed",
                severity="error",
            )

    def _execute_session_command(self, command) -> None:
        if command.action is CommandAction.NEW:
            self.action_new_session()
        elif command.action is CommandAction.SWITCH:
            self.handle_session_selected(command.argument)
        elif command.action is CommandAction.LIST:
            self.query_one("#session-sidebar").display = True
            self.query_one("#session-list").focus()
        elif command.action is CommandAction.SEARCH:
            self.query_one("#session-filter").value = command.argument
            self.query_one("#session-sidebar").display = True
        elif command.action is CommandAction.RENAME:
            self.service.rename_session(command.argument)
        elif command.action is CommandAction.DELETE:
            self.push_screen(
                ConfirmScreen("Delete the active session?"),
                self._delete_session_confirmed,
            )

    def _execute_memory_command(self, command) -> None:
        if command.action is CommandAction.LIST:
            items = self.service.list_memory()
            body = "\n".join(
                f"- `{item.id}` **{item.key}** = {item.content} ({item.kind})"
                for item in items
            ) or "No workspace memory entries."
            self.push_screen(ManagementScreen("Workspace Memory", body))
        elif command.action is CommandAction.ADD:
            self.service.add_memory(command.argument)
        elif command.action is CommandAction.DELETE:
            self.service.delete_memory(command.argument)
        elif command.action is CommandAction.CLEAR:
            self.push_screen(
                ConfirmScreen("Clear all memory for this workspace?"),
                self._clear_memory_confirmed,
            )

    def _execute_skill_command(self, command) -> None:
        if command.action is CommandAction.LIST:
            items = self.service.list_skills()
            body = "\n".join(
                f"- **{item.name}** [{item.activation}] — {item.description}"
                for item in items
            ) or "No skills found."
            self.push_screen(ManagementScreen("Skills", body))
        elif command.action is CommandAction.USE:
            self.service.use_skill(command.argument)
        elif command.action is CommandAction.OFF:
            self.service.off_skill(command.argument)
        elif command.action is CommandAction.CLEAR:
            self.service.clear_skills()

    def _execute_plugin_command(self, command) -> None:
        if command.action is CommandAction.LIST:
            items = self.service.list_plugins()
            body = (
                "Executable plugins run as **trusted local code**.\n\n"
                + (
                    "\n".join(
                        f"- **{item.name}** {item.version} [{item.status}] — "
                        f"{item.description}"
                        for item in items
                    )
                    or "No plugins found."
                )
            )
            self.push_screen(ManagementScreen("Plugins", body))
        elif command.action is CommandAction.ENABLE:
            self.service.enable_plugin(command.argument)
        elif command.action is CommandAction.DISABLE:
            self.service.disable_plugin(command.argument)

    def _show_recall(self, query: str) -> None:
        results = self.service.recall(query)
        body = "\n\n".join(
            f"- `{item.session_id}` {item.source} #{item.ordinal}: {item.excerpt}"
            for item in results
        ) or "No matching history in this workspace."
        self.push_screen(ManagementScreen(f"Recall: {query}", body))

    def _delete_session_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self.service.delete_session()
            self._refresh_all(self.service.snapshot())

    def _clear_memory_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self.service.clear_memory()
            self._refresh_all(self.service.snapshot())

    def _quit_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self.service.cancel_task()
            self.exit()

    def _offer_pending_candidate(self) -> None:
        try:
            candidates = self.service.pending_candidates()
        except Exception:
            return
        if not candidates:
            return
        candidate = candidates[0]
        question = (
            f"Remember {candidate.key} = {candidate.content} "
            f"for this workspace?"
        )
        self.push_screen(
            ConfirmScreen(question),
            lambda accepted: self._candidate_decided(candidate.id, accepted),
        )

    def _candidate_decided(self, candidate_id: str, accepted: bool) -> None:
        try:
            self.service.confirm_candidate(candidate_id, accept=accepted)
            self._refresh_all(self.service.snapshot())
        except Exception as exc:
            self.notify(f"{type(exc).__name__}: memory decision failed", severity="error")

    def _refresh_all(self, snapshot: ProductSnapshot) -> None:
        self.query_one("#session-sidebar", SessionSidebar).update_sessions(
            snapshot.sessions
        )
        self.query_one("#conversation", ConversationPane).show_snapshot(snapshot)
        self.query_one("#status-bar", ProductStatusBar).update_status(snapshot.status)
        header = self.query_one("#product-header", Static)
        header.update(
            f"Coding Agent  {snapshot.status.provider}/{snapshot.status.model}  "
            f"{snapshot.status.workspace}  {snapshot.status.session_id[:6]}"
        )

    def _apply_responsive(self, width: int) -> None:
        compact = width < 96
        self.set_class(compact, "compact")
        sidebar = self.query_one("#session-sidebar")
        sidebar.display = self._sidebar_requested and not compact

    def _set_composer_text(self, value: str) -> None:
        composer = self.query_one("#composer", Composer)
        composer.text = value
        composer.cursor_location = composer.document.end

    def _close_service_once(self) -> None:
        if self._closed_service:
            return
        self._closed_service = True
        self.service.close()


def run_tui(service: CodingAgentService) -> int:
    CodingAgentApp(service).run()
    return 0


def _help_markdown() -> str:
    commands = "\n".join(
        f"- `{item.usage}` — {item.description}" for item in command_help()
    )
    return (
        "# Coding Agent Help\n\n"
        "## Keys\n\n"
        "- `Ctrl+Enter` submit\n- `Enter` newline\n- `Ctrl+C` cancel/clear\n"
        "- `Esc` focus input\n- `Ctrl+N` new session\n- `Ctrl+B` sessions\n"
        "- `Ctrl+L` activity detail\n- `Ctrl+Q` quit\n\n"
        "## Commands\n\n" + commands
    )


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)

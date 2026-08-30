"""Responsive Textual application over the synchronous product facade."""

from __future__ import annotations

from typing import Any

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Footer, Static, TextArea

from ..application.commands import (
    CommandAction,
    CommandError,
    CommandName,
    command_help,
    parse_command,
)
from ..application.events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
)
from ..application.service import CodingAgentService
from ..application.state import AgentState, ProductSnapshot
from ..config import ConfigError
from ..model import ModelClientError
from ..plugins import PluginError
from ..session import SessionError
from ..tools.paths import WorkspacePathError
from ..tools.registry import ToolArgumentError
from .screens import (
    CommandPaletteScreen,
    ConfirmScreen,
    HelpScreen,
    ManagementScreen,
    PluginManagementScreen,
    SkillManagementScreen,
    TextPromptScreen,
)
from .widgets import (
    ActivityPane,
    Composer,
    ConversationPane,
    ProductStatusBar,
    SessionSidebar,
    SlashCommandSuggestions,
)


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
        Binding("ctrl+p", "command_palette", "Commands", priority=True),
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
        self._phase = "Ready"

    def compose(self) -> ComposeResult:
        yield Static("Coding Agent", id="product-header")
        with Horizontal(id="product-body"):
            yield SessionSidebar()
            with Vertical(id="main-panel"):
                yield ConversationPane()
                yield SlashCommandSuggestions()
                yield Composer()
            yield ActivityPane()
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
            self.query_one("#activity", ActivityPane).apply_event(
                self._last_transient_error
            )
            self._last_transient_error = None
        self._offer_pending_candidate()
        composer.focus()

    @on(TextArea.Changed, "#composer")
    def _composer_changed(self, event: TextArea.Changed) -> None:
        self.query_one(
            "#slash-suggestions", SlashCommandSuggestions
        ).update_for(event.text_area.text)

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
            self.post_message(ProductEventMessage(_exception_event(exc)))
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
        self.query_one(
            "#slash-suggestions", SlashCommandSuggestions
        ).dismiss_suggestions()
        self.query_one("#composer", Composer).focus()

    def action_accept_suggestion(self) -> bool:
        suggestions = self.query_one(
            "#slash-suggestions", SlashCommandSuggestions
        )
        value = suggestions.accept_highlighted()
        if value is None:
            return False
        self._set_composer_text(value)
        suggestions.update_for(value)
        self.query_one("#composer", Composer).focus()
        return True

    def action_navigate_suggestion(self, direction: int) -> bool:
        return self.query_one(
            "#slash-suggestions", SlashCommandSuggestions
        ).navigate(direction)

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
        activity = self.query_one("#activity", ActivityPane)
        activity.display = not activity.display

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen(_help_markdown()))

    def action_command_palette(self) -> None:
        self.push_screen(CommandPaletteScreen(), self._palette_selected)

    def action_show_skills(self) -> None:
        self.push_screen(
            SkillManagementScreen(
                self.service.list_skills(), self._manage_skill, self._show_product_error
            )
        )

    def action_show_plugins(self) -> None:
        self.push_screen(
            PluginManagementScreen(
                self.service.list_plugins(),
                self._manage_plugin,
                self._show_product_error,
            )
        )

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
        self.query_one("#activity", ActivityPane).apply_event(event)
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
        self._phase = _phase_for_event(event, current=self._phase)
        status_getter = getattr(self.service, "get_status", None)
        status = (
            status_getter()
            if callable(status_getter)
            else self.service.snapshot().status
        )
        self.query_one("#status-bar", ProductStatusBar).update_status(
            status, self._phase
        )

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
            self._show_product_error(exc)

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
            self.action_show_skills()
        elif command.action is CommandAction.USE:
            self._manage_skill("use", command.argument)
        elif command.action is CommandAction.OFF:
            self._manage_skill("off", command.argument)
        elif command.action is CommandAction.CLEAR:
            self._manage_skill("clear", "")

    def _execute_plugin_command(self, command) -> None:
        if command.action is CommandAction.LIST:
            self.action_show_plugins()
        elif command.action is CommandAction.ENABLE:
            self._manage_plugin("enable", command.argument)
        elif command.action is CommandAction.DISABLE:
            self._manage_plugin("disable", command.argument)

    def _manage_skill(self, action: str, name: str):
        if action == "use":
            self.service.use_skill(name)
        elif action == "off":
            self.service.off_skill(name)
        elif action == "clear":
            self.service.clear_skills()
        else:
            raise ValueError("unknown Skill action")
        return self.service.list_skills()

    def _manage_plugin(self, action: str, name: str):
        if action == "enable":
            self.service.enable_plugin(name)
        elif action == "disable":
            self.service.disable_plugin(name)
        else:
            raise ValueError("unknown Plugin action")
        return self.service.list_plugins()

    def _palette_selected(self, action: str | None) -> None:
        if action is None:
            return
        if action == "new_session":
            self.action_new_session()
        elif action == "switch_session":
            self._sidebar_requested = True
            sidebar = self.query_one("#session-sidebar")
            sidebar.display = True
            self.query_one("#session-list").focus()
        elif action == "skills":
            self.action_show_skills()
        elif action == "plugins":
            self.action_show_plugins()
        elif action == "memory":
            command = parse_command("/memory")
            assert command is not None
            self._execute_command(command)
        elif action == "recall":
            self.push_screen(TextPromptScreen("Recall query"), self._palette_recall)
        elif action == "toggle_activity":
            self.action_toggle_activity()
        elif action == "help":
            self.action_show_help()

    def _palette_recall(self, query: str | None) -> None:
        if query:
            self._show_recall(query)

    def _show_recall(self, query: str) -> None:
        results = self.service.recall(query)
        body = "\n\n".join(
            f"- `{item.session_id}` {item.source} #{item.ordinal}: {item.excerpt}"
            for item in results
        ) or "No matching history in this workspace."
        self.push_screen(ManagementScreen(f"Recall: {query}", body))

    def _show_product_error(self, exc: Exception) -> None:
        event = _exception_event(exc)
        self.apply_product_event(event)
        self.notify(event.title, severity="error")

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
        self.query_one("#activity", ActivityPane).show_snapshot(snapshot)
        self.query_one("#status-bar", ProductStatusBar).update_status(
            snapshot.status, self._phase
        )
        header = self.query_one("#product-header", Static)
        active = next(
            (item for item in snapshot.sessions if item.active),
            None,
        )
        session_name = active.display_name if active is not None else "Untitled"
        header.update(
            f"Coding Agent  {snapshot.status.workspace.name}  "
            f"{session_name} · {snapshot.status.session_id[:6]}"
        )

    def _apply_responsive(self, width: int) -> None:
        compact = width < 96
        self.set_class(compact, "compact")
        sidebar = self.query_one("#session-sidebar")
        sidebar.display = self._sidebar_requested and not compact
        self.query_one("#status-bar", ProductStatusBar).refresh_width()

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
        "- `Ctrl+L` toggle Activity\n- `Ctrl+P` command palette\n"
        "- `Ctrl+Q` quit\n\n"
        "## Commands\n\n" + commands
    )


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _phase_for_event(
    event: ProductEvent,
    *,
    current: str,
) -> str:
    if event.kind is ProductEventKind.TASK_STARTED:
        return "Working"
    if event.kind is ProductEventKind.MODEL_WAITING:
        return event.title or "Waiting for provider"
    if event.kind is ProductEventKind.TOOL_STARTED:
        return "Running tool"
    if event.kind is ProductEventKind.TOOL_FINISHED:
        return "Working"
    if event.kind in {
        ProductEventKind.SUBAGENT_BATCH,
        ProductEventKind.SUBAGENT_STARTED,
        ProductEventKind.SUBAGENT_FINISHED,
    }:
        return "Parallel investigation"
    if event.kind is ProductEventKind.VERIFICATION:
        return "Verifying"
    if event.kind is ProductEventKind.FILE_CHANGES:
        return "Reviewing changes"
    if event.kind is ProductEventKind.FINAL_RESPONSE:
        return "Ready"
    if event.kind is ProductEventKind.SESSION_CHANGED:
        return "Ready"
    if event.kind in {ProductEventKind.ERROR, ProductEventKind.TASK_FAILED}:
        return "Error"
    if event.kind is ProductEventKind.TASK_CANCELLED:
        return "Cancelled"
    return current


def _exception_event(exc: Exception) -> ProductEvent:
    if isinstance(exc, PluginError):
        title, detail = "Plugin Error", exc.message
    elif isinstance(exc, SessionError):
        title, detail = "Session Error", exc.message
    elif isinstance(exc, ConfigError):
        title, detail = "Configuration Error", str(exc).strip() or "invalid configuration"
    elif isinstance(exc, ModelClientError):
        title, detail = "Provider Error", str(exc).strip() or "provider request failed"
    elif isinstance(exc, (ToolArgumentError, WorkspacePathError)):
        title, detail = "Tool Error", str(exc).strip() or "tool operation failed"
    else:
        title, detail = f"Internal Error: {type(exc).__name__}", ""
    return ProductEvent(
        ProductEventKind.ERROR,
        _utc_now(),
        None,
        None,
        None,
        title,
        detail,
        ActivityStatus.FAILED,
        source=ActivitySource.ERROR,
    )

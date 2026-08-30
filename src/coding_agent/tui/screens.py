"""Small reusable product dialogs; no business logic lives here."""

from __future__ import annotations

from collections.abc import Callable

from textual import on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from ..application.state import PluginView, SkillView


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Grid(id="confirm-dialog"):
            yield Label(self.question, id="confirm-question")
            yield Button("Confirm", id="confirm", variant="error")
            yield Button("Cancel", id="cancel", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextPromptScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, initial: str = "") -> None:
        super().__init__()
        self.prompt_title = title
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Grid(id="prompt-dialog"):
            yield Label(self.prompt_title)
            yield Input(value=self.initial, id="prompt-value")
            yield Button("Save", id="save", variant="primary")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#prompt-value", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            value = self.query_one("#prompt-value", Input).value.strip()
            self.dismiss(value or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameSessionScreen(ModalScreen[str | None]):
    """Validated Session rename dialog; persistence stays in the service."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Grid(id="rename-session-dialog"):
            yield Label("Rename Session", classes="dialog-title")
            yield Label(f"Current: {self.current_name}", id="rename-session-current")
            yield Input(
                value=self.current_name,
                max_length=80,
                id="rename-session-value",
            )
            yield Static("", id="rename-session-error")
            yield Button("Rename", id="rename-session-confirm", variant="primary")
            yield Button("Cancel", id="rename-session-cancel")

    def on_mount(self) -> None:
        self.query_one("#rename-session-error", Static).display = False
        self.query_one("#rename-session-value", Input).focus()

    @on(Input.Submitted, "#rename-session-value")
    def _input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-session-confirm":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#rename-session-value", Input).value.strip()
        if not value:
            error = self.query_one("#rename-session-error", Static)
            error.update("Session name cannot be empty.")
            error.display = True
            self.query_one("#rename-session-value", Input).focus()
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeleteSessionScreen(ModalScreen[bool]):
    """Session-specific destructive confirmation with accurate scope."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def compose(self) -> ComposeResult:
        with Grid(id="delete-session-dialog"):
            yield Label("Delete Session?", classes="dialog-title")
            yield Static(
                f"{self.session_name}\n\n"
                "This will delete this session's persisted conversation history.\n"
                "Workspace memory will not be deleted.",
                id="delete-session-message",
            )
            yield Button(
                "Delete", id="delete-session-confirm", variant="error"
            )
            yield Button("Cancel", id="delete-session-cancel", variant="primary")

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-session-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, markdown: str) -> None:
        super().__init__()
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog"):
            yield Markdown(self.markdown)
            yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ManagementScreen(ModalScreen[None]):
    """Read-only rendering for management results and recall excerpts."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, markdown: str) -> None:
        super().__init__()
        self.title = title
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="management-dialog"):
            yield Markdown(f"# {self.title}\n\n{self.markdown}")
            yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class CommandPaletteScreen(ModalScreen[str | None]):
    """Keyboard-first navigation over existing App actions."""

    BINDINGS = [("escape", "close", "Close")]
    _ACTIONS = (
        ("new_session", "New Session"),
        ("switch_session", "Switch Session"),
        ("skills", "Skills"),
        ("plugins", "Plugins"),
        ("memory", "Memory"),
        ("recall", "Recall"),
        ("toggle_activity", "Toggle Activity"),
        ("widen_sessions", "Widen Sessions"),
        ("narrow_sessions", "Narrow Sessions"),
        ("widen_activity", "Widen Activity"),
        ("narrow_activity", "Narrow Activity"),
        ("help", "Help"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.plain_text = "\n".join(label for _action, label in self._ACTIONS)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-dialog"):
            yield Label("Command Palette", classes="dialog-title")
            yield OptionList(
                *(Option(label, id=action) for action, label in self._ACTIONS),
                id="palette-list",
                compact=True,
            )

    def on_mount(self) -> None:
        options = self.query_one("#palette-list", OptionList)
        options.highlighted = 0
        options.focus()

    @on(OptionList.OptionSelected, "#palette-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_close(self) -> None:
        self.dismiss(None)


class _ResourceManagementScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self,
        title: str,
        items: tuple[SkillView, ...] | tuple[PluginView, ...],
        on_action: Callable[
            [str, str], tuple[SkillView, ...] | tuple[PluginView, ...]
        ],
        on_error: Callable[[Exception], None] | None = None,
        *,
        warning: str = "",
        primary_label: str,
        secondary_label: str,
        allow_clear: bool,
    ) -> None:
        super().__init__()
        self.resource_title = title
        self.items = items
        self.on_action = on_action
        self.on_error = on_error
        self.warning = warning
        self.primary_label = primary_label
        self.secondary_label = secondary_label
        self.allow_clear = allow_clear

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-dialog"):
            yield Label(self.resource_title, classes="dialog-title")
            yield Static(self.warning, id="resource-warning")
            yield OptionList(id="resource-list", compact=True)
            yield Markdown("", id="resource-detail")
            with Horizontal(classes="dialog-actions"):
                yield Button(self.primary_label, id="resource-primary", variant="primary")
                yield Button(self.secondary_label, id="resource-secondary")
                if self.allow_clear:
                    yield Button("Clear All", id="resource-clear", variant="warning")
                yield Button("Close", id="resource-close")

    def on_mount(self) -> None:
        self._render_items()
        options = self.query_one("#resource-list", OptionList)
        options.focus()

    @on(OptionList.OptionHighlighted, "#resource-list")
    def _highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._render_detail()

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resource-close":
            self.dismiss(None)
            return
        item = self._selected_item()
        if event.button.id == "resource-clear":
            action, name = "clear", ""
        elif item is None:
            return
        elif event.button.id == "resource-primary":
            action, name = self._primary_action(), item.name
        elif event.button.id == "resource-secondary":
            action, name = self._secondary_action(), item.name
        else:
            return
        try:
            self.items = self.on_action(action, name)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(exc)
            else:
                self.app.notify(
                    f"{self.resource_title} operation failed", severity="error"
                )
            return
        self._render_items(preferred_name=name)

    def action_close(self) -> None:
        self.dismiss(None)

    def _render_items(self, preferred_name: str | None = None) -> None:
        options = self.query_one("#resource-list", OptionList)
        options.set_options(
            Option(self._option_label(item), id=str(index))
            for index, item in enumerate(self.items)
        )
        index = next(
            (
                index
                for index, item in enumerate(self.items)
                if item.name == preferred_name
            ),
            0 if self.items else None,
        )
        options.highlighted = index
        self.query_one("#resource-warning", Static).display = bool(self.warning)
        self._render_detail()

    def _selected_item(self) -> SkillView | PluginView | None:
        index = self.query_one("#resource-list", OptionList).highlighted
        if index is None or index >= len(self.items):
            return None
        return self.items[index]

    def _render_detail(self) -> None:
        item = self._selected_item()
        body = "No resources found." if item is None else self._detail(item)
        self.query_one("#resource-detail", Markdown).update(body)

    def _option_label(self, item: SkillView | PluginView) -> str:
        raise NotImplementedError

    def _detail(self, item: SkillView | PluginView) -> str:
        raise NotImplementedError

    def _primary_action(self) -> str:
        raise NotImplementedError

    def _secondary_action(self) -> str:
        raise NotImplementedError


class SkillManagementScreen(_ResourceManagementScreen):
    def __init__(
        self,
        items: tuple[SkillView, ...],
        on_action: Callable[[str, str], tuple[SkillView, ...]],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__(
            "Skills",
            items,
            on_action,
            on_error,
            primary_label="Activate",
            secondary_label="Deactivate",
            allow_clear=True,
        )

    def _option_label(self, item: SkillView | PluginView) -> str:
        assert isinstance(item, SkillView)
        return f"{item.name}  [{item.activation}]  {item.scope}"

    def _detail(self, item: SkillView | PluginView) -> str:
        assert isinstance(item, SkillView)
        return (
            f"## {item.name}\n\n"
            f"Status: **{item.activation}**  \nScope: `{item.scope}`\n\n"
            f"{item.description}"
        )

    def _primary_action(self) -> str:
        return "use"

    def _secondary_action(self) -> str:
        return "off"


class PluginManagementScreen(_ResourceManagementScreen):
    def __init__(
        self,
        items: tuple[PluginView, ...],
        on_action: Callable[[str, str], tuple[PluginView, ...]],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        warning = (
            items[0].trust_warning
            if items
            else "Executable plugins run as trusted local code."
        )
        super().__init__(
            "Plugins",
            items,
            on_action,
            on_error,
            warning=warning,
            primary_label="Enable",
            secondary_label="Disable",
            allow_clear=False,
        )

    def _option_label(self, item: SkillView | PluginView) -> str:
        assert isinstance(item, PluginView)
        return f"{item.name}  {item.version}  [{item.status}]"

    def _detail(self, item: SkillView | PluginView) -> str:
        assert isinstance(item, PluginView)
        return (
            f"## {item.name}\n\n"
            f"Version: `{item.version}`  \nStatus: **{item.status}**\n\n"
            f"{item.description}"
        )

    def _primary_action(self) -> str:
        return "enable"

    def _secondary_action(self) -> str:
        return "disable"

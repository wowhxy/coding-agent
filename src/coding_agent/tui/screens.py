"""Small reusable product dialogs; no business logic lives here."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown


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

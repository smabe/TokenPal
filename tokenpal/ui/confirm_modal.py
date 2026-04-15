"""Yes/no confirmation modal used by the agent loop for gated tools.

Pattern matches SelectionModal: pure screen, dismisses with a bool, caller
persists. Escape always resolves to False (no).
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    DEFAULT_CSS: ClassVar[str] = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal #confirm-body {
        width: 60;
        max-width: 90%;
        height: auto;
        max-height: 60%;
        background: $surface;
        border: thick $warning;
        padding: 1 2;
    }
    ConfirmModal #confirm-title {
        text-style: bold;
        padding-bottom: 1;
    }
    ConfirmModal #confirm-text {
        padding-bottom: 1;
    }
    ConfirmModal #confirm-buttons {
        height: auto;
        padding-top: 1;
        align-horizontal: right;
    }
    ConfirmModal Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="confirm-body"):
            yield Label(self._title, id="confirm-title")
            yield Label(self._body, id="confirm-text")
            with Horizontal(id="confirm-buttons"):
                yield Button("Deny", id="deny-btn", variant="default")
                yield Button("Allow", id="allow-btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#deny-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow-btn":
            self.dismiss(True)
        elif event.button.id == "deny-btn":
            self.dismiss(False)

    def action_deny(self) -> None:
        self.dismiss(False)

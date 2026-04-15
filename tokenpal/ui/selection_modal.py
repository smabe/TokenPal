"""Reusable multi-group SelectionList modal.

Textual's `SelectionList` does not accept separators/headers/disabled options
inside a single list (runtime rejects `Separator` / plain `Option`). We work
around that by stacking one `SelectionList` per section, each preceded by a
`Label` header, inside a scrollable container.

The modal is pure — no I/O, no config mutation. On save it dismisses with a
`{section_title: [selected_values]}` dict; the caller persists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, SelectionList
from textual.widgets.selection_list import Selection


@dataclass(frozen=True)
class SelectionItem:
    """One row inside a modal group. ``locked`` means the row cannot be toggled."""

    value: str
    label: str
    initial: bool = False
    locked: bool = False


@dataclass(frozen=True)
class SelectionGroup:
    """A labeled section of the modal. Title doubles as the result-dict key."""

    title: str
    items: tuple[SelectionItem, ...]
    help_text: str = ""


class SelectionModal(ModalScreen[dict[str, list[str]] | None]):
    """Generic multi-group checkbox modal.

    Result: ``dict[group_title, list[selected_value]]`` on save, ``None`` on cancel.
    Locked items are always reported as selected (they show up read-only but
    keep the result honest so callers don't need a special case).
    """

    DEFAULT_CSS: ClassVar[str] = """
    SelectionModal {
        align: center middle;
    }
    SelectionModal #modal-body {
        width: 70;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    SelectionModal #modal-title {
        text-style: bold;
        padding-bottom: 1;
    }
    SelectionModal .group-header {
        text-style: bold;
        color: $accent;
        padding-top: 1;
    }
    SelectionModal .group-help {
        color: $text-muted;
        padding-bottom: 1;
    }
    SelectionModal SelectionList {
        height: auto;
        max-height: 12;
        border: none;
        padding: 0 1;
    }
    SelectionModal #modal-buttons {
        height: auto;
        padding-top: 1;
        align-horizontal: right;
    }
    SelectionModal Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, groups: list[SelectionGroup]) -> None:
        super().__init__()
        self._title = title
        self._groups = groups

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="modal-body"):
            yield Label(self._title, id="modal-title")
            for group in self._groups:
                yield Label(group.title, classes="group-header")
                if group.help_text:
                    yield Label(group.help_text, classes="group-help")
                toggleable = [i for i in group.items if not i.locked]
                if toggleable:
                    selections = [
                        Selection(i.label, i.value, initial_state=i.initial)
                        for i in toggleable
                    ]
                    yield SelectionList[str](*selections, id=self._list_id(group))
                locked = [i for i in group.items if i.locked]
                for item in locked:
                    yield Label(f"  [x] {item.label}  (always on)")
            with Horizontal(id="modal-buttons"):
                yield Button("Cancel", id="cancel-btn", variant="default")
                yield Button("Save", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        # Land focus on the first toggleable list so keyboard users hit checkboxes,
        # not the Save button. Fall back to the Save button if every group is locked.
        for group in self._groups:
            if any(not i.locked for i in group.items):
                self.query_one(f"#{self._list_id(group)}", SelectionList).focus()
                return
        self.query_one("#save-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.dismiss(self._collect())
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for group in self._groups:
            locked = {i.value for i in group.items if i.locked}
            selected: set[str] = set()
            if any(not i.locked for i in group.items):
                sl = self.query_one(f"#{self._list_id(group)}", SelectionList)
                selected = set(sl.selected)
            result[group.title] = sorted(locked | selected)
        return result

    @staticmethod
    def _list_id(group: SelectionGroup) -> str:
        # Textual widget IDs must be valid CSS identifiers — sanitize the title.
        safe = "".join(c if c.isalnum() else "-" for c in group.title.lower())
        return f"group-{safe}"

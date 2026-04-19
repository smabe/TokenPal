"""Modal for /options — umbrella settings screen.

Exposes:
  - Chat history: digit-only Input for max_persisted + "Clear history now"
    Button (propagates a flag in the result; app layer wipes the chat_log
    table via MemoryStore.clear_chat_log()).
  - Settings shortcuts: launcher Buttons for "Cloud LLM...", "Senses
    toggles...", "Tools...". Each dismisses the modal with ``navigate_to``
    set so the app-layer callback opens the matching existing modal via
    its existing helper (``_open_cloud_modal``, ``_open_senses_modal``,
    ``_open_tools_modal``) — no logic duplicated.

Sanitization: the max_persisted Input uses ``type="integer"`` plus
``restrict=r"[0-9]*"`` so typed non-digits are rejected at the widget.
``_collect()`` re-casts with int() inside try/except and clamps via
chatlog_writer.clamp_max_persisted before handing the value to the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from tokenpal.config.chatlog_writer import (
    MAX_PERSISTED,
    MIN_PERSISTED,
    clamp_max_persisted,
)

NavigateTo = Literal["cloud", "senses", "tools"]


@dataclass(frozen=True)
class OptionsModalState:
    """Current state fed into the modal."""

    max_persisted: int
    persist_enabled: bool


@dataclass(frozen=True)
class OptionsModalResult:
    """Result from the modal. ``navigate_to`` non-None means the user
    clicked a launcher button and the app should open that modal next
    (any max_persisted edit is discarded in that case).
    ``clear_history`` True means the user clicked Clear; the app wipes
    both the persisted table and the live widget.
    """

    max_persisted: int
    clear_history: bool = False
    navigate_to: NavigateTo | None = None


class OptionsModal(ModalScreen[OptionsModalResult | None]):
    """Umbrella options modal. Save returns an OptionsModalResult; Cancel /
    Esc returns None."""

    DEFAULT_CSS: ClassVar[str] = """
    OptionsModal {
        align: center middle;
    }
    OptionsModal #options-body {
        width: 70%;
        min-width: 40;
        max-width: 120;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    OptionsModal Label {
        width: 100%;
        height: auto;
    }
    OptionsModal #options-title {
        text-style: bold;
        padding-bottom: 1;
    }
    OptionsModal .section-header {
        text-style: bold;
        color: $accent;
        padding-top: 1;
    }
    OptionsModal .section-help {
        color: $text-muted;
        padding-bottom: 1;
        padding-left: 2;
    }
    OptionsModal Input {
        margin-bottom: 1;
        width: 100%;
    }
    OptionsModal .launcher-row {
        height: auto;
        width: 100%;
        padding-top: 1;
    }
    OptionsModal #options-buttons {
        height: auto;
        width: 100%;
        padding-top: 1;
        align-horizontal: right;
    }
    OptionsModal Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, state: OptionsModalState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        s = self._state
        with VerticalScroll(id="options-body"):
            yield Label("Options", id="options-title")

            # --------------------------------------------------------------
            # Chat history section
            # --------------------------------------------------------------
            yield Label("Chat history", classes="section-header")
            persist_line = (
                "Persist enabled" if s.persist_enabled
                else "Persist disabled (edit config.toml to re-enable)"
            )
            yield Label(
                f"How many chat entries to remember across restarts "
                f"({MIN_PERSISTED}-{MAX_PERSISTED}). {persist_line}.",
                classes="section-help",
            )
            yield Input(
                value=str(s.max_persisted),
                placeholder="200",
                id="max-persisted-input",
                type="integer",
                restrict=r"[0-9]*",
                max_length=6,
            )
            with Horizontal(classes="launcher-row"):
                yield Button(
                    "Clear history now",
                    id="clear-history-btn",
                    variant="warning",
                )

            # --------------------------------------------------------------
            # Settings shortcuts — launchers for existing modals
            # --------------------------------------------------------------
            yield Label("Settings shortcuts", classes="section-header")
            yield Label(
                "Open another settings screen (this modal closes first).",
                classes="section-help",
            )
            with Horizontal(classes="launcher-row"):
                yield Button("Cloud LLM...", id="launch-cloud-btn")
                yield Button("Senses...", id="launch-senses-btn")
                yield Button("Tools...", id="launch-tools-btn")

            # --------------------------------------------------------------
            # Bottom save / cancel
            # --------------------------------------------------------------
            with Horizontal(id="options-buttons"):
                yield Button("Cancel", id="cancel-btn", variant="default")
                yield Button("Save", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        # Focus the topmost editable widget so the scroll view stays at the
        # top; focusing Save would yank it to the bottom.
        try:
            self.query_one("#max-persisted-input", Input).focus()
        except Exception:
            pass
        self.query_one("#options-body", VerticalScroll).scroll_home(
            animate=False
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "save-btn":
            self.dismiss(self._collect(clear_history=False))
        elif btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id == "clear-history-btn":
            # Clear is an explicit user action; persist the current input
            # value too so saving a new max_persisted + clearing in one shot
            # works as expected.
            self.dismiss(self._collect(clear_history=True))
        elif btn_id == "launch-cloud-btn":
            self.dismiss(self._nav("cloud"))
        elif btn_id == "launch-senses-btn":
            self.dismiss(self._nav("senses"))
        elif btn_id == "launch-tools-btn":
            self.dismiss(self._nav("tools"))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self, *, clear_history: bool) -> OptionsModalResult:
        return OptionsModalResult(
            max_persisted=self._read_max_persisted(),
            clear_history=clear_history,
            navigate_to=None,
        )

    def _nav(self, target: NavigateTo) -> OptionsModalResult:
        # Navigate-only: carry the current (unedited) max_persisted so the
        # app layer doesn't accidentally clobber the stored value with 0.
        return OptionsModalResult(
            max_persisted=self._state.max_persisted,
            clear_history=False,
            navigate_to=target,
        )

    def _read_max_persisted(self) -> int:
        try:
            raw = self.query_one("#max-persisted-input", Input).value.strip()
        except Exception:
            return self._state.max_persisted
        if not raw:
            return self._state.max_persisted
        try:
            parsed = int(raw)
        except ValueError:
            # restrict=r"[0-9]*" should make this unreachable; belt-and-suspenders.
            return self._state.max_persisted
        return clamp_max_persisted(parsed)

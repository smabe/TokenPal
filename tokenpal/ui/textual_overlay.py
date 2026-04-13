"""Textual-based overlay — rich TUI with proper input handling."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Input, Static

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import register_overlay

log = logging.getLogger(__name__)

_CSS_PATH = Path(__file__).parent / "textual_overlay.tcss"


class AutoHideSpeech(Message):
    """Posted when the speech bubble auto-hide timer fires."""


class HeaderWidget(Static):
    """Centered buddy name with border lines."""

    def __init__(self, buddy_name: str) -> None:
        super().__init__(id="header")
        self._buddy_name = buddy_name

    def on_mount(self) -> None:
        self._refresh_header()

    def on_resize(self) -> None:
        self._refresh_header()

    def _refresh_header(self) -> None:
        width = self.size.width or 40
        label = f" {self._buddy_name} "
        pad = max(0, (width - len(label)) // 2)
        self.update(f"{'─' * pad}{label}{'─' * pad}")


class SpeechBubbleWidget(Static):
    """Speech bubble with typing animation."""

    def __init__(self) -> None:
        super().__init__(id="speech")
        self._full_text: str = ""
        self._bubble: SpeechBubble | None = None
        self._typing_index: int = 0
        self._typing_timer: Timer | None = None
        self._hide_timer: Timer | None = None

    def start_typing(self, bubble: SpeechBubble) -> None:
        self._cancel_timers()
        self._bubble = bubble
        self._full_text = bubble.text
        self._typing_index = 0
        self.display = True
        self._render_partial()
        self._typing_timer = self.set_interval(0.03, self._advance_typing)

    def _advance_typing(self) -> None:
        self._typing_index += 1
        if self._typing_index >= len(self._full_text):
            if self._typing_timer:
                self._typing_timer.stop()
                self._typing_timer = None
            self._render_partial()
            self._start_auto_hide()
        else:
            self._render_partial()

    def _render_partial(self) -> None:
        if not self._bubble:
            return
        partial = SpeechBubble(
            text=self._full_text[: self._typing_index + 1],
            style=self._bubble.style,
            max_width=self._bubble.max_width,
        )
        self.update("\n".join(partial.render()))

    def _start_auto_hide(self) -> None:
        if self._bubble and self._bubble.persistent:
            return
        delay = max(10.0, len(self._full_text) * 0.15)
        self._hide_timer = self.set_timer(delay, self._fire_auto_hide)

    def _fire_auto_hide(self) -> None:
        self._hide_timer = None
        self.post_message(AutoHideSpeech())

    def hide(self) -> None:
        self._cancel_timers()
        self._bubble = None
        self.display = False

    def _cancel_timers(self) -> None:
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        if self._hide_timer:
            self._hide_timer.stop()
            self._hide_timer = None


class BuddyWidget(Static):
    """ASCII buddy art."""

    def __init__(self) -> None:
        super().__init__(id="buddy")

    def show_frame(self, frame: BuddyFrame) -> None:
        self.update("\n".join(frame.lines))


class StatusBarWidget(Static):
    """Bottom status text."""

    def __init__(self) -> None:
        super().__init__("Ctrl+C to quit", id="status-bar")

    def set_text(self, text: str) -> None:
        self.update(text)


class TokenPalApp(App[None]):
    """Main Textual application for TokenPal."""

    CSS_PATH = str(_CSS_PATH)
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", show=False)]

    def __init__(self, overlay: TextualOverlay) -> None:
        super().__init__()
        self._overlay = overlay

    def compose(self) -> ComposeResult:
        yield HeaderWidget(self._overlay._buddy_name)
        yield Static(id="spacer")
        yield SpeechBubbleWidget()
        yield BuddyWidget()
        yield StatusBarWidget()
        yield Input(placeholder="Type a message or /command...", id="user-input")

    def on_mount(self) -> None:
        buddy = self.query_one(BuddyWidget)
        buddy.show_frame(BuddyFrame.get("idle"))
        self._overlay._is_running = True
        log.info("TextualOverlay ready")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        log.info("Input: %s", text[:30])
        if text.startswith("/"):
            if self._overlay._command_callback:
                self._overlay._command_callback(text)
        else:
            if self._overlay._input_callback:
                self._overlay._input_callback(text)

    def on_auto_hide_speech(self, _event: AutoHideSpeech) -> None:
        self._overlay.hide_speech()


@register_overlay
class TextualOverlay(AbstractOverlay):
    overlay_name = "textual"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._buddy_name = config.get("buddy_name", "TokenPal")
        self._app: TokenPalApp | None = None
        self._is_running = False
        self._input_callback: Callable[[str], None] | None = None
        self._command_callback: Callable[[str], None] | None = None

    def setup(self) -> None:
        self._app = TokenPalApp(self)

    def show_buddy(self, frame: BuddyFrame) -> None:
        if not self._app or not self._is_running:
            return
        self._app.call_from_thread(
            self._app.query_one(BuddyWidget).show_frame, frame
        )

    def show_speech(self, bubble: SpeechBubble) -> None:
        if not self._app or not self._is_running:
            return

        def _show() -> None:
            self._app.query_one(BuddyWidget).show_frame(  # type: ignore[union-attr]
                BuddyFrame.get("talking")
            )
            self._app.query_one(SpeechBubbleWidget).start_typing(bubble)  # type: ignore[union-attr]

        self._app.call_from_thread(_show)

    def hide_speech(self) -> None:
        if not self._app or not self._is_running:
            return

        def _hide() -> None:
            self._app.query_one(SpeechBubbleWidget).hide()  # type: ignore[union-attr]
            self._app.query_one(BuddyWidget).show_frame(  # type: ignore[union-attr]
                BuddyFrame.get("idle")
            )

        self._app.call_from_thread(_hide)

    def update_status(self, text: str) -> None:
        if not self._app or not self._is_running:
            return
        self._app.call_from_thread(
            self._app.query_one(StatusBarWidget).set_text, text
        )

    def set_input_callback(self, callback: Callable[[str], None]) -> None:
        self._input_callback = callback

    def set_command_callback(self, callback: Callable[[str], None]) -> None:
        self._command_callback = callback

    def run_loop(self) -> None:
        if self._app:
            self._app.run()

    def schedule_callback(
        self, callback: Callable[[], None], delay_ms: int = 0
    ) -> None:
        if not self._app or not self._is_running:
            return
        if delay_ms <= 0:
            self._app.call_from_thread(callback)
        else:
            self._app.call_from_thread(
                lambda: self._app.set_timer(delay_ms / 1000.0, lambda: callback())  # type: ignore[union-attr]
            )

    def teardown(self) -> None:
        self._is_running = False
        if self._app:
            self._app.call_from_thread(self._app.exit)

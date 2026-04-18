"""Textual-based overlay — rich TUI with proper input handling."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.errors import MarkupError
from rich.markup import escape as _esc_markup
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Resize
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Input, Static

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.confirm_modal import ConfirmModal
from tokenpal.ui.registry import register_overlay
from tokenpal.ui.selection_modal import SelectionGroup, SelectionModal

log = logging.getLogger(__name__)

_CSS_PATH = Path(__file__).parent / "textual_overlay.tcss"
_BUDDY_PANEL_PADDING = 4
_CHAT_LOG_MIN_SPACE = 30
_SPEECH_SCROLL_PADDING = 4
_MIN_BORDERED_REGION_WIDTH = 36
_BUBBLE_HOLD_MIN_S = 2.5
_BUBBLE_HOLD_PER_CHAR_S = 0.05


# --- Messages (all thread-safe via post_message) ---


class ShowSpeech(Message):
    def __init__(self, bubble: SpeechBubble) -> None:
        self.bubble = bubble
        super().__init__()


class HideSpeech(Message):
    pass


class ShowBuddy(Message):
    def __init__(self, frame: BuddyFrame) -> None:
        self.frame = frame
        super().__init__()


class UpdateStatus(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class LogBuddyMessage(Message):
    def __init__(self, text: str, *, markup: bool = False, url: str | None = None) -> None:
        self.text = text
        self.markup = markup
        self.url = url
        super().__init__()


class LogUserMessage(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ClearLog(Message):
    pass


class ToggleChatLog(Message):
    pass


class RunCallback(Message):
    def __init__(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        self.callback = callback
        self.delay_ms = delay_ms
        super().__init__()


class LoadVoiceFrames(Message):
    def __init__(self, frames: dict[str, BuddyFrame]) -> None:
        self.frames = frames
        super().__init__()


class ClearVoiceFrames(Message):
    pass


class RequestExit(Message):
    pass


class OpenSelectionModal(Message):
    def __init__(
        self,
        title: str,
        groups: list[SelectionGroup],
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> None:
        self.title = title
        self.groups = groups
        self.on_save = on_save
        super().__init__()


class OpenConfirmModal(Message):
    def __init__(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> None:
        self.title = title
        self.body = body
        self.on_result = on_result
        super().__init__()


# --- Widgets ---


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


class SpeechBubbleWidget(VerticalScroll):
    """Scrollable speech bubble with typing animation."""

    def __init__(self) -> None:
        super().__init__(id="speech-scroll")
        self._body: Static = Static(id="speech")
        self._full_text: str = ""
        self._bubble: SpeechBubble | None = None
        self._source_bubble: SpeechBubble | None = None
        self._typing_index: int = 0
        self._typing_timer: Timer | None = None
        self._hide_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield self._body

    @property
    def is_active(self) -> bool:
        return self._bubble is not None

    @property
    def current_bubble(self) -> SpeechBubble | None:
        return self._bubble

    @property
    def source_bubble(self) -> SpeechBubble | None:
        return self._source_bubble

    def start_typing(
        self, bubble: SpeechBubble, source: SpeechBubble | None = None
    ) -> None:
        self._prime(bubble, source, typing_index=0)
        self._typing_timer = self.set_interval(0.03, self._advance_typing)

    def show_immediate(
        self, bubble: SpeechBubble, source: SpeechBubble | None = None
    ) -> None:
        self._prime(bubble, source, typing_index=max(0, len(bubble.text) - 1))
        self._start_auto_hide()

    def swap_variant(self, bubble: SpeechBubble) -> None:
        if self._bubble is None:
            return
        self._bubble = bubble
        self._render_partial()

    def _prime(
        self, bubble: SpeechBubble, source: SpeechBubble | None, typing_index: int
    ) -> None:
        self._cancel_timers()
        self._bubble = bubble
        self._source_bubble = source or bubble
        self._full_text = bubble.text
        self._typing_index = typing_index
        self.display = True
        self._render_partial()

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
        partial = dataclasses.replace(
            self._bubble, text=self._full_text[: self._typing_index + 1]
        )
        self._body.update("\n".join(partial.render()))
        self.scroll_end(animate=False)

    def _start_auto_hide(self) -> None:
        if self._bubble and self._bubble.persistent:
            return
        delay = max(_BUBBLE_HOLD_MIN_S, len(self._full_text) * _BUBBLE_HOLD_PER_CHAR_S)
        self._hide_timer = self.set_timer(delay, self._fire_auto_hide)

    @property
    def is_typing(self) -> bool:
        return self._typing_timer is not None

    def _fire_auto_hide(self) -> None:
        self._hide_timer = None
        self.post_message(HideSpeech())

    def hide(self) -> None:
        self._cancel_timers()
        self._bubble = None
        self._source_bubble = None
        self.display = False

    def _cancel_timers(self) -> None:
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        if self._hide_timer:
            self._hide_timer.stop()
            self._hide_timer = None


class BuddyWidget(Static):
    """ASCII buddy art with optional Rich markup and idle blink animation."""

    def __init__(self) -> None:
        super().__init__(id="buddy", markup=True)
        self._custom_frames: dict[str, BuddyFrame] = {}
        self._blink_timer: Timer | None = None
        self._blink_state: bool = False
        self._is_talking: bool = False
        self._cached_max_width: int = self._compute_max_frame_width()

    def set_custom_frames(self, frames: dict[str, BuddyFrame]) -> None:
        """Load voice-specific frames and start idle blink if idle_alt exists."""
        self._custom_frames = frames
        self._cached_max_width = self._compute_max_frame_width()
        self._stop_blink()
        if "idle_alt" in frames and "idle" in frames:
            self._blink_timer = self.set_interval(4.0, self._toggle_blink)
        if not self._is_talking:
            self.show_frame(self._get_frame("idle"))

    def clear_custom_frames(self) -> None:
        """Revert to generic frames."""
        self._custom_frames = {}
        self._cached_max_width = self._compute_max_frame_width()
        self._stop_blink()
        self.show_frame(BuddyFrame.get("idle"))

    def show_frame(self, frame: BuddyFrame) -> None:
        self._is_talking = frame.name == "talking"
        if self._is_talking:
            self._blink_state = False
        self._render_frame(frame)

    def _render_frame(self, frame: BuddyFrame) -> None:
        """Update the widget with a frame; fall back to plain text on MarkupError.

        Repair passes in ``ascii_renderer._fix_markup`` run at load time, but a
        profile generated before those passes landed — or a particularly
        creative LLM output — can still smuggle malformed markup into
        ``frame.lines``. Catching here keeps a bad frame from crashing the app.
        """
        try:
            self.update("\n".join(frame.lines))
        except MarkupError as exc:
            log.warning(
                "buddy frame %s has unparseable markup (%s); rendering plain",
                frame.name, exc,
            )
            self.update("\n".join(_esc_markup(line) for line in frame.lines))

    def _get_frame(self, name: str) -> BuddyFrame:
        if name in self._custom_frames:
            return self._custom_frames[name]
        return BuddyFrame.get(name)

    def _toggle_blink(self) -> None:
        if self._is_talking:
            return
        self._blink_state = not self._blink_state
        name = "idle_alt" if self._blink_state else "idle"
        self._render_frame(self._get_frame(name))

    def max_frame_width(self) -> int:
        return self._cached_max_width

    def _compute_max_frame_width(self) -> int:
        frames = self._custom_frames or {
            "idle": BuddyFrame.get("idle"),
            "talking": BuddyFrame.get("talking"),
        }
        widths = [
            Text.from_markup(line).cell_len
            for frame in frames.values()
            for line in frame.lines
        ]
        return max(widths, default=20)

    def _stop_blink(self) -> None:
        if self._blink_timer:
            self._blink_timer.stop()
            self._blink_timer = None
        self._blink_state = False


_MOOD_COLORS: dict[str, str] = {
    "snarky": "#00ff88",
    "impressed": "#ffcc00",
    "bored": "#888888",
    "concerned": "#ff6666",
    "hyper": "#ff88ff",
    "sleepy": "#6688cc",
}


class StatusBarWidget(Static):
    """Bottom status text with mood-colored first segment."""

    def __init__(self) -> None:
        super().__init__("Ctrl+C to quit", id="status-bar", markup=True)

    def set_text(self, text: str) -> None:
        parts = text.split(" | ", maxsplit=1)
        mood = parts[0].lower()
        color = _MOOD_COLORS.get(mood, "#666666")
        if len(parts) > 1:
            markup = f"[{color}]{parts[0]}[/] | {parts[1]}"
        else:
            markup = f"[{color}]{text}[/]"
        self.update(markup)


# --- App ---


class TokenPalApp(App[None]):
    """Main Textual application for TokenPal."""

    CSS_PATH = str(_CSS_PATH)
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("f1", "command_help", "Help", show=False, priority=True),
        Binding("f2", "toggle_chat_log", "Toggle chat log", show=False, priority=True),
        Binding("ctrl+l", "command_clear", "Clear", show=False, priority=True),
    ]

    def __init__(self, overlay: TextualOverlay) -> None:
        super().__init__()
        self._overlay = overlay
        self._chat_log_user_hidden: bool = False
        self._pending_bubble: SpeechBubble | None = None
        self._last_region_size: tuple[int, int] | None = None
        self._chat_log_lines: list[str] = []
        self._link_urls: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="buddy-panel"):
            yield HeaderWidget(self._overlay._buddy_name)
            with Vertical(id="speech-region"):
                yield Static(id="spacer")
                yield SpeechBubbleWidget()
            with Vertical(id="buddy-footer"):
                yield BuddyWidget()
                yield Input(placeholder="Type a message or /command...", id="user-input")
                yield StatusBarWidget()
        with VerticalScroll(id="chat-log"):
            yield Static(id="chat-log-content", markup=True)

    def on_mount(self) -> None:
        self._overlay._is_running = True
        self._chat_log_widget = self.query_one("#chat-log-content", Static)
        self._chat_log_scroll = self.query_one("#chat-log", VerticalScroll)
        buddy = self.query_one(BuddyWidget)
        if self._overlay._pending_voice_frames:
            buddy.set_custom_frames(self._overlay._pending_voice_frames)
            self._overlay._pending_voice_frames = None
        else:
            buddy.show_frame(BuddyFrame.get("idle"))
        self._apply_buddy_panel_min_width()
        log.info("TextualOverlay ready")

    def _apply_buddy_panel_min_width(self) -> None:
        buddy = self.query_one(BuddyWidget)
        panel = self.query_one("#buddy-panel", Vertical)
        panel.styles.min_width = buddy.max_frame_width() + _BUDDY_PANEL_PADDING

    def on_resize(self, _event: Resize) -> None:
        self._apply_chat_log_visibility()
        self._evict_oversized_bubble()

    def _apply_chat_log_visibility(self) -> None:
        if self._chat_log_user_hidden:
            return
        buddy = self.query_one(BuddyWidget)
        threshold = buddy.max_frame_width() + _BUDDY_PANEL_PADDING + _CHAT_LOG_MIN_SPACE
        chat_log = self.query_one("#chat-log", VerticalScroll)
        chat_log.display = self.size.width >= threshold

    def _evict_oversized_bubble(self) -> None:
        speech = self.query_one(SpeechBubbleWidget)
        if not (speech.is_active or self._pending_bubble):
            return
        region = self.query_one("#speech-region", Vertical)
        region_size = (region.size.width, region.size.height)
        if region_size == self._last_region_size:
            return
        self._last_region_size = region_size
        self._rechoose_active_variant(speech)
        self._promote_pending(speech)

    def _rechoose_active_variant(self, speech: SpeechBubbleWidget) -> None:
        source = speech.source_bubble
        current = speech.current_bubble
        if not (speech.is_active and source and current):
            return
        variant = self._choose_bubble_variant(source)
        if variant is None:
            self.post_message(HideSpeech())
        elif variant.borderless != current.borderless or variant.max_width != current.max_width:
            speech.swap_variant(variant)

    def _promote_pending(self, speech: SpeechBubbleWidget) -> None:
        if not self._pending_bubble or speech.is_active:
            return
        variant = self._choose_bubble_variant(self._pending_bubble)
        if variant is None:
            return
        source = self._pending_bubble
        self._pending_bubble = None
        self._begin_bubble(variant, source=source, skip_typing=True)

    # --- Keyboard shortcuts ---

    def action_command_help(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/help")

    def action_command_clear(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/clear")

    def action_toggle_chat_log(self) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        chat_log.display = not chat_log.display
        self._chat_log_user_hidden = not chat_log.display

    # --- Input handling ---

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
            # Log user message to chat, then send to brain
            self._log_user(text)
            if self._overlay._input_callback:
                self._overlay._input_callback(text)

    _MAX_CHAT_LOG_LINES = 500

    def _append_log(
        self, name: str, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        ts = datetime.now().strftime("%I:%M %p")
        safe = text if markup else _esc_markup(text)
        line = f"──────────────────────\n\\[{ts}]\n{_esc_markup(name)}: {safe}"
        if url:
            idx = len(self._link_urls)
            self._link_urls.append(url)
            line += (
                f"\n[underline #5599ff][@click=app.open_chat_link(\"{idx}\")]"
                f"{_esc_markup(url)}[/][/underline #5599ff]"
            )
        lines = self._chat_log_lines
        lines.append(line)
        if len(lines) > self._MAX_CHAT_LOG_LINES:
            del lines[: len(lines) - self._MAX_CHAT_LOG_LINES]
        self._chat_log_widget.update("\n".join(lines))
        self._chat_log_scroll.scroll_end(animate=False)

    def action_open_chat_link(self, link_id: str) -> None:
        idx = int(link_id)
        if 0 <= idx < len(self._link_urls):
            self.open_url(self._link_urls[idx])

    def _log_user(self, text: str) -> None:
        self._append_log("You", text)

    def _log_buddy(self, text: str, *, markup: bool = False, url: str | None = None) -> None:
        name = (self._overlay._voice_name or self._overlay._buddy_name).capitalize()
        self._append_log(name, text, markup=markup, url=url)

    # --- Message handlers (all run on app thread) ---

    def on_show_speech(self, message: ShowSpeech) -> None:
        self._log_buddy(message.bubble.text)
        variant = self._choose_bubble_variant(message.bubble)
        if variant is None:
            self._pending_bubble = message.bubble
            return
        speech = self.query_one(SpeechBubbleWidget)
        current = speech.source_bubble if speech.is_active else None
        # Don't let a transient comment clobber a persistent progress bubble.
        if current is not None and current.persistent and not message.bubble.persistent:
            return
        # Persistent-over-persistent skips typing; everything else clobbers with typing.
        skip = message.bubble.persistent and current is not None and current.persistent
        self._pending_bubble = None
        self._begin_bubble(variant, source=message.bubble, skip_typing=skip)

    def on_hide_speech(self, _message: HideSpeech) -> None:
        self.query_one(SpeechBubbleWidget).hide()
        buddy = self.query_one(BuddyWidget)
        buddy.show_frame(buddy._get_frame("idle"))

    def _begin_bubble(
        self, bubble: SpeechBubble, source: SpeechBubble, skip_typing: bool = False
    ) -> None:
        buddy = self.query_one(BuddyWidget)
        buddy.show_frame(buddy._get_frame("talking"))
        speech = self.query_one(SpeechBubbleWidget)
        if skip_typing:
            speech.show_immediate(bubble, source=source)
        else:
            speech.start_typing(bubble, source=source)

    def _choose_bubble_variant(self, bubble: SpeechBubble) -> SpeechBubble | None:
        # None signals "no variant fits — park as pending until resize-up".
        region = self.query_one("#speech-region", Vertical)
        region_h = region.size.height
        region_w = region.size.width
        if region_h <= 0 or region_w <= 0:
            return bubble
        bordered_max = max(1, min(bubble.max_width, region_w - _SPEECH_SCROLL_PADDING))
        if region_w >= _MIN_BORDERED_REGION_WIDTH:
            bordered = dataclasses.replace(
                bubble, max_width=bordered_max, borderless=False
            )
            if len(bordered.render()) <= region_h:
                return bordered
        borderless = dataclasses.replace(
            bubble,
            max_width=max(1, region_w - _SPEECH_SCROLL_PADDING),
            borderless=True,
        )
        if len(borderless.render()) <= region_h:
            return borderless
        return None

    def on_show_buddy(self, message: ShowBuddy) -> None:
        self.query_one(BuddyWidget).show_frame(message.frame)

    def on_load_voice_frames(self, message: LoadVoiceFrames) -> None:
        self.query_one(BuddyWidget).set_custom_frames(message.frames)
        self._apply_buddy_panel_min_width()

    def on_clear_voice_frames(self, _message: ClearVoiceFrames) -> None:
        self.query_one(BuddyWidget).clear_custom_frames()
        self._apply_buddy_panel_min_width()

    def on_update_status(self, message: UpdateStatus) -> None:
        self.query_one(StatusBarWidget).set_text(message.text)

    def on_log_buddy_message(self, message: LogBuddyMessage) -> None:
        self._log_buddy(message.text, markup=message.markup, url=message.url)

    def on_log_user_message(self, message: LogUserMessage) -> None:
        self._log_user(message.text)

    def on_clear_log(self, _message: ClearLog) -> None:
        self._chat_log_lines.clear()
        self._link_urls.clear()
        self._chat_log_widget.update("")

    def on_toggle_chat_log(self, _message: ToggleChatLog) -> None:
        self.action_toggle_chat_log()

    def on_run_callback(self, message: RunCallback) -> None:
        if message.delay_ms <= 0:
            message.callback()
        else:
            self.set_timer(message.delay_ms / 1000.0, message.callback)

    def on_request_exit(self, _message: RequestExit) -> None:
        self.exit()

    def on_open_selection_modal(self, message: OpenSelectionModal) -> None:
        modal = SelectionModal(message.title, message.groups)
        self.push_screen(modal, message.on_save)

    def on_open_confirm_modal(self, message: OpenConfirmModal) -> None:
        modal = ConfirmModal(message.title, message.body)
        self.push_screen(modal, message.on_result)


# --- Overlay ---


@register_overlay
class TextualOverlay(AbstractOverlay):
    overlay_name = "textual"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._buddy_name = config.get("buddy_name", "TokenPal")
        self._voice_name: str = ""
        self._app: TokenPalApp | None = None
        self._is_running = False
        self._input_callback: Callable[[str], None] | None = None
        self._command_callback: Callable[[str], None] | None = None
        self._pending_voice_frames: dict[str, BuddyFrame] | None = None

    def _post(self, message: Message) -> None:
        """Post a message to the app. Thread-safe, no-op if app not ready."""
        if self._app and self._is_running:
            self._app.post_message(message)

    def setup(self) -> None:
        self._app = TokenPalApp(self)

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._post(ShowBuddy(frame))

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._post(ShowSpeech(bubble))

    def hide_speech(self) -> None:
        self._post(HideSpeech())

    def update_status(self, text: str) -> None:
        self._post(UpdateStatus(text))

    def load_voice_frames(self, frames: dict[str, BuddyFrame]) -> None:
        if not self._is_running:
            self._pending_voice_frames = frames
            return
        self._post(LoadVoiceFrames(frames))

    def clear_voice_frames(self) -> None:
        self._post(ClearVoiceFrames())

    def log_buddy_message(
        self, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        self._post(LogBuddyMessage(text, markup=markup, url=url))

    def log_user_message(self, text: str) -> None:
        self._post(LogUserMessage(text))

    def clear_log(self) -> None:
        self._post(ClearLog())

    def toggle_chat_log(self) -> None:
        self._post(ToggleChatLog())

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
        self._post(RunCallback(callback, delay_ms))

    def open_selection_modal(
        self,
        title: str,
        groups: Any,
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> bool:
        if not (self._app and self._is_running):
            return False
        self._post(OpenSelectionModal(title, list(groups), on_save))
        return True

    def open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> bool:
        if not (self._app and self._is_running):
            return False
        self._post(OpenConfirmModal(title, body, on_result))
        return True

    def teardown(self) -> None:
        self._is_running = False
        if self._app:
            self._app.post_message(RequestExit())

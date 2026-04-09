"""Console overlay — renders TokenPal directly in the terminal."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from typing import Any, Callable

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble, render_buddy_with_bubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import register_overlay

log = logging.getLogger(__name__)

# ANSI escape codes
_GREEN = "\033[38;2;0;255;136m"
_WHITE = "\033[38;2;220;220;220m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CLEAR_SCREEN = "\033[2J\033[H"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"

# Typing animation speed (seconds per character)
_TYPING_SPEED = 0.03


@register_overlay
class ConsoleOverlay(AbstractOverlay):
    overlay_name = "console"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._current_frame = BuddyFrame.get("idle")
        self._current_bubble: SpeechBubble | None = None
        self._hide_job: threading.Timer | None = None
        self._callbacks: list[tuple[Callable[[], None], float]] = []
        self._lock = threading.Lock()
        self._running = False
        self._buddy_name = config.get("buddy_name", "TokenPal")
        self._status_text: str = "Ctrl+C to quit"

        # Typing animation state
        self._full_text: str = ""
        self._typing_index: int = 0
        self._typing_active: bool = False
        self._last_type_time: float = 0.0

    def setup(self) -> None:
        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        self._render()
        log.info("ConsoleOverlay ready")

    def _render(self) -> None:
        """Redraw the entire console display, bottom-anchored."""
        term_width = shutil.get_terminal_size().columns
        term_height = shutil.get_terminal_size().lines

        content: list[str] = []

        # Header
        header = f" {self._buddy_name} "
        hpad = (term_width - len(header)) // 2
        content.append("")
        content.append(f"{_DIM}{'─' * hpad}{_RESET}{_GREEN}{header}{_RESET}{_DIM}{'─' * hpad}{_RESET}")
        content.append("")

        # Speech bubble or status (above buddy)
        if self._current_bubble:
            # During typing, show partial text; after typing, show full text
            if self._typing_active:
                partial = SpeechBubble(
                    text=self._full_text[:self._typing_index],
                    style=self._current_bubble.style,
                    max_width=self._current_bubble.max_width,
                )
                bubble_lines = partial.render()
            else:
                bubble_lines = self._current_bubble.render()
            for bl in bubble_lines:
                pad = max(0, (term_width - len(bl)) // 2)
                content.append(f"{_WHITE}{' ' * pad}{bl}{_RESET}")
        else:
            status = "zzz..."
            pad = max(0, (term_width - len(status)) // 2)
            content.append(f"{_DIM}{' ' * pad}{status}{_RESET}")

        content.append("")

        # Buddy (always centered)
        buddy_lines = self._current_frame.lines
        for bl in buddy_lines:
            pad = max(0, (term_width - len(bl)) // 2)
            content.append(f"{_GREEN}{' ' * pad}{bl}{_RESET}")

        content.append("")

        # Bottom border
        content.append(f"{_DIM}{'─' * term_width}{_RESET}")

        # Status bar (bottom-most)
        content.append(f"{_DIM}  {self._status_text}{_RESET}")

        # Fill remaining space above with blank lines to push to bottom
        blank_lines = max(0, term_height - len(content))
        output_lines = [""] * blank_lines + content

        # Write all at once to avoid flicker
        output = _CLEAR_SCREEN + "\n".join(output_lines)
        sys.stdout.write(output)
        sys.stdout.flush()

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._current_frame = frame
        self._render()

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._current_bubble = bubble
        self._current_frame = BuddyFrame.get("talking")

        # Start typing animation
        self._full_text = bubble.text
        self._typing_index = 0
        self._typing_active = True
        self._last_type_time = time.monotonic()

        # Cancel any pending hide job
        if self._hide_job:
            self._hide_job.cancel()
            self._hide_job = None

        self._render()

    def _finish_typing(self) -> None:
        """Called when the typing animation completes."""
        self._typing_active = False
        self._render()

        # Start auto-hide timer now that typing is done
        display_s = max(10.0, len(self._full_text) * 0.15)
        self._hide_job = threading.Timer(display_s, self.hide_speech)
        self._hide_job.daemon = True
        self._hide_job.start()

    def update_status(self, text: str) -> None:
        self._status_text = text
        if not self._typing_active:
            self._render()

    def hide_speech(self) -> None:
        self._current_bubble = None
        self._current_frame = BuddyFrame.get("idle")
        self._hide_job = None
        self._typing_active = False
        self._render()

    def run_loop(self) -> None:
        """Block the main thread, processing scheduled callbacks."""
        self._running = True
        try:
            while self._running:
                # Advance typing animation
                if self._typing_active:
                    now = time.monotonic()
                    if now - self._last_type_time >= _TYPING_SPEED:
                        self._typing_index += 1
                        self._last_type_time = now
                        if self._typing_index >= len(self._full_text):
                            self._finish_typing()
                        else:
                            self._render()

                # Process any pending callbacks
                with self._lock:
                    now = time.monotonic()
                    ready = [(cb, t) for cb, t in self._callbacks if t <= now]
                    self._callbacks = [(cb, t) for cb, t in self._callbacks if t > now]

                for cb, _ in ready:
                    try:
                        cb()
                    except Exception:
                        log.exception("Callback error")

                # Adaptive sleep: fast during typing, slow otherwise
                time.sleep(0.03 if self._typing_active else 0.1)
        except KeyboardInterrupt:
            self._running = False

    def schedule_callback(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        with self._lock:
            run_at = time.monotonic() + (delay_ms / 1000.0)
            self._callbacks.append((callback, run_at))

    def teardown(self) -> None:
        self._running = False
        if self._hide_job:
            self._hide_job.cancel()
        sys.stdout.write(_SHOW_CURSOR + "\n")
        sys.stdout.flush()

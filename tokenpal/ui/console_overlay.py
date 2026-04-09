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

# ANSI color codes
_GREEN = "\033[38;2;0;255;136m"
_WHITE = "\033[38;2;220;220;220m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CLEAR_SCREEN = "\033[2J\033[H"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


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

    def setup(self) -> None:
        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        self._render()
        log.info("ConsoleOverlay ready")

    def _render(self) -> None:
        """Redraw the entire console display."""
        term_width = shutil.get_terminal_size().columns

        lines: list[str] = []

        # Header
        lines.append("")
        header = f" {self._buddy_name} "
        pad = (term_width - len(header)) // 2
        lines.append(f"{_DIM}{'─' * pad}{_RESET}{_GREEN}{header}{_RESET}{_DIM}{'─' * pad}{_RESET}")
        lines.append("")

        # Speech bubble or status (above buddy)
        if self._current_bubble:
            bubble_lines = self._current_bubble.render()
            for bl in bubble_lines:
                pad = max(0, (term_width - len(bl)) // 2)
                lines.append(f"{_WHITE}{' ' * pad}{bl}{_RESET}")
        else:
            status = "zzz..."
            pad = max(0, (term_width - len(status)) // 2)
            lines.append(f"{_DIM}{' ' * pad}{status}{_RESET}")

        lines.append("")

        # Buddy (always centered, below bubble)
        buddy_lines = self._current_frame.lines
        for bl in buddy_lines:
            pad = max(0, (term_width - len(bl)) // 2)
            lines.append(f"{_GREEN}{' ' * pad}{bl}{_RESET}")

        lines.append("")

        # Bottom border
        lines.append(f"{_DIM}{'─' * term_width}{_RESET}")

        # Sense status line
        lines.append(f"{_DIM}  Ctrl+C to quit{_RESET}")

        # Write all at once to avoid flicker
        output = _CLEAR_SCREEN + "\n".join(lines)
        sys.stdout.write(output)
        sys.stdout.flush()

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._current_frame = frame
        self._render()

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._current_bubble = bubble
        self._current_frame = BuddyFrame.get("talking")
        self._render()

        # Auto-hide after duration
        if self._hide_job:
            self._hide_job.cancel()
        display_s = max(4.0, len(bubble.text) * 0.1)
        self._hide_job = threading.Timer(display_s, self.hide_speech)
        self._hide_job.daemon = True
        self._hide_job.start()

    def hide_speech(self) -> None:
        self._current_bubble = None
        self._current_frame = BuddyFrame.get("idle")
        self._hide_job = None
        self._render()

    def run_loop(self) -> None:
        """Block the main thread, processing scheduled callbacks."""
        self._running = True
        try:
            while self._running:
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

                time.sleep(0.1)
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

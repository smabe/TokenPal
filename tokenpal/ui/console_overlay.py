"""Console overlay — renders TokenPal directly in the terminal."""

from __future__ import annotations

import atexit
import logging
import shutil
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
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

# Try to import Unix-only terminal modules
try:
    import select
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False

# Try to import msvcrt (Windows only)
try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False


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

        # Input state
        self._input_buffer: str = ""
        self._input_callback: Callable[[str], None] | None = None
        self._command_callback: Callable[[str], None] | None = None
        self._orig_termios: list[Any] | None = None
        self._render_dirty: bool = False

    def setup(self) -> None:
        # Enter cbreak mode for character-by-character input
        if _HAS_TERMIOS and sys.stdin.isatty():
            self._orig_termios = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
            atexit.register(self._restore_terminal)

        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        self._render()
        log.info("ConsoleOverlay ready")

    def _restore_terminal(self) -> None:
        """Restore terminal to original state. Safe to call multiple times."""
        if self._orig_termios is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._orig_termios)
            except (termios.error, ValueError):
                pass
            self._orig_termios = None

    def set_input_callback(self, callback: Callable[[str], None]) -> None:
        self._input_callback = callback

    def set_command_callback(self, callback: Callable[[str], None]) -> None:
        self._command_callback = callback

    def _render(self) -> None:
        """Redraw the entire console display, bottom-anchored."""
        term_width = shutil.get_terminal_size().columns
        term_height = shutil.get_terminal_size().lines

        content: list[str] = []

        # Header
        header = f" {self._buddy_name} "
        hpad = (term_width - len(header)) // 2
        content.append("")
        content.append(
            f"{_DIM}{'─' * hpad}{_RESET}"
            f"{_GREEN}{header}{_RESET}"
            f"{_DIM}{'─' * hpad}{_RESET}"
        )
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

        # Input line
        prompt = "> "
        max_input = term_width - len(prompt) - 2  # room for cursor + margin
        visible_buf = self._input_buffer[-max_input:] if max_input > 0 else ""
        content.append(f"  {_WHITE}{prompt}{visible_buf}_{_RESET}")

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

        # Persistent bubbles stay until replaced (e.g. training progress)
        if self._current_bubble and self._current_bubble.persistent:
            return

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

    def _handle_char(self, ch: str) -> None:
        """Process a single input character (shared across platforms)."""
        if ch in ("\r", "\n"):
            self._on_submit()
        elif ch in ("\x7f", "\x08"):  # Backspace (DEL on Unix, BS on Windows)
            if self._input_buffer:
                self._input_buffer = self._input_buffer[:-1]
                self._render_dirty = True
        elif ch.isprintable():
            self._input_buffer += ch
            self._render_dirty = True

    def _poll_input(self) -> None:
        """Non-blocking stdin read. Processes one character per call."""
        if _HAS_TERMIOS and sys.stdin.isatty():
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return

            ch = sys.stdin.read(1)

            if ch == "\x1b":
                # Escape sequence — drain all trailing bytes (variable length)
                while True:
                    trail, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if not trail:
                        break
                    sys.stdin.read(1)
            else:
                self._handle_char(ch)
        elif _HAS_MSVCRT:
            if not msvcrt.kbhit():  # type: ignore[attr-defined]
                return
            ch = msvcrt.getwch()  # type: ignore[attr-defined]
            if ch.isprintable() or ch in ("\r", "\n", "\x08"):
                self._handle_char(ch)

    def _on_submit(self) -> None:
        """Handle Enter key — dispatch command or send to brain."""
        text = self._input_buffer.strip()
        self._input_buffer = ""
        self._render()

        if not text:
            return

        log.info("Input: %s", text)

        if text.startswith("/"):
            if self._command_callback:
                self._command_callback(text)
        else:
            if self._input_callback:
                self._input_callback(text)

    def run_loop(self) -> None:
        """Block the main thread, processing scheduled callbacks."""
        self._running = True
        try:
            while self._running:
                # Poll for keyboard input
                self._poll_input()

                # Coalesce input redraws into the main loop tick
                if self._render_dirty:
                    self._render_dirty = False
                    self._render()

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
                    self._callbacks = [
                        (cb, t) for cb, t in self._callbacks if t > now
                    ]

                for cb, _ in ready:
                    try:
                        cb()
                    except Exception:
                        log.exception("Callback error")

                # Adaptive sleep: fast during typing, slow otherwise
                time.sleep(0.03 if self._typing_active else 0.1)
        except KeyboardInterrupt:
            self._running = False

    def schedule_callback(
        self, callback: Callable[[], None], delay_ms: int = 0
    ) -> None:
        with self._lock:
            run_at = time.monotonic() + (delay_ms / 1000.0)
            self._callbacks.append((callback, run_at))

    def teardown(self) -> None:
        self._running = False
        if self._hide_job:
            self._hide_job.cancel()
        self._restore_terminal()
        sys.stdout.write(_SHOW_CURSOR + "\n")
        sys.stdout.flush()

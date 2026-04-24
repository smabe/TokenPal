"""Tkinter overlay — cross-platform always-on-top window."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from typing import Any

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import register_overlay
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_MARGIN = 20

# Dark semi-transparent background that actually renders cleanly
_BG_COLOR = "#1a1a2e"
_FG_COLOR = "#00ff88"
_BUBBLE_FG = "#ffffff"


@register_overlay
class TkOverlay(AbstractOverlay):
    overlay_name = "tkinter"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._position = config.get("position", "bottom_right")
        self._font_family = config.get("font_family") or "Menlo"
        self._font_size = config.get("font_size", 13)
        self._root: tk.Tk | None = None
        self._buddy_label: tk.Label | None = None
        self._bubble_label: tk.Label | None = None
        self._current_frame = BuddyFrame.get("idle")
        self._current_bubble: SpeechBubble | None = None
        self._hide_job: str | None = None
        self._placed = False

    def setup(self) -> None:
        self._root = tk.Tk()
        self._root.title("TokenPal")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)

        plat = current_platform()
        if plat == "darwin":
            # Use alpha transparency instead of systemTransparent to avoid render glitches
            self._root.attributes("-alpha", 0.92)
        elif plat == "windows":
            trans_color = "#010101"
            self._root.attributes("-transparentcolor", trans_color)

        self._root.config(bg=_BG_COLOR)

        # Container frame
        container = tk.Frame(self._root, bg=_BG_COLOR)
        container.pack(padx=8, pady=8)

        # Speech bubble label (above buddy, hidden initially)
        self._bubble_label = tk.Label(
            container,
            text="",
            font=(self._font_family, self._font_size - 1),
            fg=_BUBBLE_FG,
            bg=_BG_COLOR,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=350,
        )
        # Don't pack yet — only shown when there's a bubble

        # Buddy label
        self._buddy_label = tk.Label(
            container,
            text="\n".join(self._current_frame.lines),
            font=(self._font_family, self._font_size),
            fg=_FG_COLOR,
            bg=_BG_COLOR,
            justify=tk.LEFT,
            anchor=tk.W,
        )
        self._buddy_label.pack()

        # Position once
        self._position_window()
        self._placed = True
        log.info("TkOverlay ready at %s", self._position)

    def _position_window(self) -> None:
        assert self._root is not None
        self._root.update_idletasks()
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()

        # Fixed size so it doesn't jump around
        win_w = 420
        win_h = 280
        self._root.geometry(f"{win_w}x{win_h}")

        dock_offset = 80  # space for macOS dock
        positions = {
            "bottom_right": (screen_w - win_w - _MARGIN, screen_h - win_h - _MARGIN - dock_offset),
            "bottom_left": (_MARGIN, screen_h - win_h - _MARGIN - dock_offset),
            "top_right": (screen_w - win_w - _MARGIN, _MARGIN),
            "top_left": (_MARGIN, _MARGIN),
        }
        x, y = positions.get(self._position, positions["bottom_right"])
        self._root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._current_frame = frame
        self._refresh()

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._current_bubble = bubble
        self._current_frame = BuddyFrame.get("talking")
        self._refresh()

        if self._root:
            if self._hide_job:
                self._root.after_cancel(self._hide_job)
            display_ms = max(4000, len(bubble.text) * 100)
            self._hide_job = self._root.after(display_ms, self.hide_speech)

    def hide_speech(self) -> None:
        self._current_bubble = None
        self._current_frame = BuddyFrame.get("idle")
        self._hide_job = None
        self._refresh()

    def _refresh(self) -> None:
        if self._buddy_label is None or self._bubble_label is None:
            return

        # Update buddy
        self._buddy_label.config(text="\n".join(self._current_frame.lines))

        # Update bubble
        if self._current_bubble:
            bubble_lines = self._current_bubble.render()
            self._bubble_label.config(text="\n".join(bubble_lines))
            self._bubble_label.pack(before=self._buddy_label, pady=(0, 4))
        else:
            self._bubble_label.pack_forget()

    def run_loop(self) -> None:
        assert self._root is not None
        self._root.mainloop()

    def schedule_callback(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        if self._root:
            self._root.after(delay_ms, callback)

    def teardown(self) -> None:
        if self._root:
            self._root.destroy()
            self._root = None

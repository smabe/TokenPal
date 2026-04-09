"""Tkinter overlay — cross-platform transparent always-on-top window."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any, Callable

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble, render_buddy_with_bubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import register_overlay
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

# Positioning offsets from screen edge
_MARGIN = 20


@register_overlay
class TkOverlay(AbstractOverlay):
    overlay_name = "tkinter"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._position = config.get("position", "bottom_right")
        self._font_family = config.get("font_family", "Courier")
        self._font_size = config.get("font_size", 14)
        self._root: tk.Tk | None = None
        self._label: tk.Label | None = None
        self._current_frame = BuddyFrame.get("idle")
        self._current_bubble: SpeechBubble | None = None
        self._hide_job: str | None = None

    def setup(self) -> None:
        self._root = tk.Tk()
        self._root.title("TokenPal")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)

        plat = current_platform()
        if plat == "darwin":
            # macOS transparency
            self._root.attributes("-transparent", True)
            self._root.config(bg="systemTransparent")
            bg_color = "systemTransparent"
        elif plat == "windows":
            # Windows transparency via transparent color
            trans_color = "#010101"
            self._root.attributes("-transparentcolor", trans_color)
            self._root.config(bg=trans_color)
            bg_color = trans_color
        else:
            # Linux — best effort
            self._root.attributes("-alpha", 0.9)
            bg_color = "#1a1a2e"
            self._root.config(bg=bg_color)

        self._label = tk.Label(
            self._root,
            text="\n".join(self._current_frame.lines),
            font=(self._font_family, self._font_size),
            fg="#00ff88",
            bg=bg_color,
            justify=tk.LEFT,
            anchor=tk.SW,
        )
        self._label.pack(padx=10, pady=10)

        self._position_window()
        log.info("TkOverlay ready at %s", self._position)

    def _position_window(self) -> None:
        assert self._root is not None
        self._root.update_idletasks()
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        win_w = self._root.winfo_reqwidth()
        win_h = self._root.winfo_reqheight()

        positions = {
            "bottom_right": (screen_w - win_w - _MARGIN, screen_h - win_h - _MARGIN - 60),
            "bottom_left": (_MARGIN, screen_h - win_h - _MARGIN - 60),
            "top_right": (screen_w - win_w - _MARGIN, _MARGIN),
            "top_left": (_MARGIN, _MARGIN),
        }
        x, y = positions.get(self._position, positions["bottom_right"])
        self._root.geometry(f"+{x}+{y}")

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._current_frame = frame
        self._refresh()

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._current_bubble = bubble
        self._current_frame = BuddyFrame.get("talking")
        self._refresh()

        # Auto-hide after a duration based on text length
        if self._root:
            if self._hide_job:
                self._root.after_cancel(self._hide_job)
            display_ms = max(3000, len(bubble.text) * 80)
            self._hide_job = self._root.after(display_ms, self.hide_speech)

    def hide_speech(self) -> None:
        self._current_bubble = None
        self._current_frame = BuddyFrame.get("idle")
        self._hide_job = None
        self._refresh()

    def _refresh(self) -> None:
        if self._label is None:
            return
        text = render_buddy_with_bubble(self._current_frame, self._current_bubble)
        self._label.config(text=text)
        if self._root:
            self._root.update_idletasks()
            self._position_window()

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

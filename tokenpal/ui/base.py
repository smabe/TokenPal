"""Base class for UI overlays."""

from __future__ import annotations

import abc
from typing import Any, Callable, ClassVar

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble


class AbstractOverlay(abc.ABC):
    """Base class for platform overlay windows.

    Subclasses declare:
        overlay_name: matches config ui.overlay value (e.g. "tkinter")
        platforms: tuple of supported platforms
    """

    overlay_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abc.abstractmethod
    def setup(self) -> None:
        """Create the window. Must run on main thread."""

    @abc.abstractmethod
    def show_buddy(self, frame: BuddyFrame) -> None:
        """Render the ASCII buddy."""

    @abc.abstractmethod
    def show_speech(self, bubble: SpeechBubble) -> None:
        """Show a speech bubble near the buddy."""

    @abc.abstractmethod
    def hide_speech(self) -> None:
        """Hide the speech bubble."""

    @abc.abstractmethod
    def run_loop(self) -> None:
        """Start the platform event loop. Blocks on main thread."""

    @abc.abstractmethod
    def schedule_callback(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        """Thread-safe way to schedule work on the UI thread."""

    @abc.abstractmethod
    def teardown(self) -> None:
        """Destroy the window and clean up."""

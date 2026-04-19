"""Base class for UI overlays."""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Any, ClassVar

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

    def update_status(self, text: str) -> None:
        """Update the status bar text. Optional — overlays may ignore."""

    def log_user_message(self, text: str) -> None:
        """Append a user message to the chat log. Optional."""

    def log_buddy_message(
        self, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        """Append a buddy message to the chat log. Optional."""

    def clear_log(self) -> None:
        """Clear the chat log. Optional."""

    def set_input_callback(self, callback: Callable[[str], None]) -> None:
        """Register handler for user text input. Optional."""

    def set_command_callback(self, callback: Callable[[str], None]) -> None:
        """Register handler for slash commands. Optional."""

    def open_selection_modal(
        self,
        title: str,
        groups: Any,
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> bool:
        """Open a multi-group SelectionList modal. Returns True if the overlay
        supports modals, False otherwise (caller should fall back to text UI).
        """
        return False

    def open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> bool:
        """Open a yes/no confirmation modal. Returns True if supported,
        False otherwise (caller must choose a safe default — usually deny)."""
        return False

    def open_cloud_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /cloud settings modal. Returns True if supported. Caller
        falls back to the text /cloud subcommands when the overlay has no
        modal support."""
        return False

    def open_options_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /options umbrella modal. Returns True if supported."""
        return False

    def load_chat_history(
        self,
        entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        """Seed the chat-log widget with persisted rows before live traffic.

        Entries are (timestamp, speaker, text, url) in chronological order
        (oldest first). Optional — console / tkinter overlays noop."""

    @abc.abstractmethod
    def run_loop(self) -> None:
        """Start the platform event loop. Blocks on main thread."""

    @abc.abstractmethod
    def schedule_callback(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        """Thread-safe way to schedule work on the UI thread."""

    @abc.abstractmethod
    def teardown(self) -> None:
        """Destroy the window and clean up."""

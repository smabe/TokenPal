"""Base class for UI overlays.

`AbstractOverlay` is the adapter seam between the brain and any frontend
(Textual, console, tkinter, Qt). Methods fall into two tiers:

- Abstract: every overlay must implement (``setup``, ``show_buddy``,
  ``show_speech``, ``hide_speech``, ``run_loop``, ``schedule_callback``,
  ``teardown``).
- Optional with no-op defaults: capability surface the brain may invoke on
  any overlay without ``hasattr`` probing. Overlays that lack the feature
  inherit a safe no-op; the ones that implement it override.

The no-op defaults are deliberate — they kill silent ``hasattr(overlay,
...)`` drift in callers (``app.py`` used to gate on ``hasattr(overlay,
"set_mood")`` etc., which caused features to vanish quietly when a new
overlay missed a method).
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Any, ClassVar

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.buddy_environment import EnvironmentSnapshot


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

    def set_buddy_reaction_callback(self, callback: Callable[[str], None]) -> None:
        """Register handler for buddy physical reactions ("poke"/"shake").
        Optional — only the Textual overlay emits these today.
        """

    def load_voice_frames(
        self,
        frames: dict[str, BuddyFrame],
        mood_frames: dict[str, dict[str, BuddyFrame]] | None = None,
    ) -> None:
        """Swap in voice-specific ASCII art. Optional — overlays without
        custom frame support no-op and keep the default buddy."""

    def clear_voice_frames(self) -> None:
        """Revert to the default built-in frames. Optional."""

    def set_mood(self, mood: str) -> None:
        """Swap to a mood-specific frame set. Optional."""

    def set_voice_name(self, name: str) -> None:
        """Record the active voice's display name (used for speech-bubble
        speaker labels). Optional — overlays that don't render a speaker
        label ignore this."""

    def toggle_chat_log(self) -> None:
        """Show/hide the chat log widget. Optional — overlays without a
        separate chat pane no-op."""

    def set_chat_history_opacity(self, opacity: float) -> None:
        """Set the chat history window's background opacity (0.0–1.0).
        Optional — overlays without a painted chat panel no-op."""

    def set_environment_provider(
        self,
        provider: Callable[[], EnvironmentSnapshot] | None,
    ) -> None:
        """Wire the brain's ``environment_snapshot`` getter so the overlay
        can pull weather/idle/sensitive state for its own render loop.
        Optional — overlays without a particle/physics layer no-op."""

    def set_chat_persist_callback(
        self,
        persist: Callable[[str, str, str | None], None],
        clear: Callable[[], None],
    ) -> None:
        """Wire chat-log write-through. Optional — overlays without a
        persisted chat log no-op."""

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

    def open_voice_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /voice management modal. Returns True if supported.
        Caller falls back to the text /voice usage string when the
        overlay has no modal support."""
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

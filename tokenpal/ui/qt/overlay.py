"""Qt frontend — adapter implementation for ``AbstractOverlay``.

Thread model: the brain runs on a daemon thread and calls adapter
methods; Qt widgets must be touched only from the main thread. Every
brain-invoked method wraps its work in a 0-arg callable and emits it
on ``_UIBridge.dispatch``, which is connected with
``QueuedConnection`` — Qt automatically queues the call onto the UI
thread.

Pre-setup calls (before ``setup()`` has constructed the widgets) are
buffered and drained in setup().
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from tokenpal.ui.ascii_renderer import BUDDY_IDLE, BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.buddy_environment import EnvironmentSnapshot
from tokenpal.ui.qt import ensure_qapplication
from tokenpal.ui.qt.buddy_window import BuddyWindow
from tokenpal.ui.qt.chat_window import ChatWindow
from tokenpal.ui.qt.modals import ConfirmDialog, SelectionDialog, _focus_dialog
from tokenpal.ui.qt.options_dialog import OptionsDialog
from tokenpal.ui.qt.platform import (
    apply_macos_accessory_mode,
    apply_macos_stay_visible,
    warn_wayland_limitations,
)
from tokenpal.ui.qt.speech_bubble import SpeechBubble as BubbleWidget
from tokenpal.ui.qt.tray import BuddyTrayIcon
from tokenpal.ui.registry import register_overlay
from tokenpal.ui.selection_modal import SelectionGroup

log = logging.getLogger(__name__)

_BUBBLE_HIDE_DELAY_MS = 6500  # how long a bubble lingers before auto-hide
_BUBBLE_HOVER_OFFSET_Y = 16    # px above the buddy window


class _UIBridge(QObject):
    """Marshals arbitrary no-arg callables from any thread onto the Qt
    main thread via a queued-connection signal."""

    dispatch = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.dispatch.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("Qt UI bridge dispatch raised")


@register_overlay
class QtOverlay(AbstractOverlay):
    overlay_name: ClassVar[str] = "qt"
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._buddy_name: str = config.get("buddy_name", "TokenPal")
        self._font_family: str = config.get("font_family", "Courier")
        self._font_size: int = int(config.get("font_size", 14))

        self._app: QApplication | None = None
        self._bridge: _UIBridge | None = None
        self._buddy: BuddyWindow | None = None
        self._bubble: BubbleWidget | None = None
        self._chat: ChatWindow | None = None
        self._tray: BuddyTrayIcon | None = None
        self._hide_bubble_timer: QTimer | None = None
        self._env_provider: Callable[[], EnvironmentSnapshot] | None = None

        # Callbacks registered by app.py — the overlay forwards user input
        # and slash commands to the brain.
        self._input_callback: Callable[[str], None] | None = None
        self._command_callback: Callable[[str], None] | None = None
        self._buddy_reaction_callback: Callable[[str], None] | None = None
        self._chat_persist_callback: (
            Callable[[str, str, str | None], None] | None
        ) = None
        self._chat_clear_callback: Callable[[], None] | None = None

        # Pre-setup buffers. The brain may call any adapter method before
        # setup() runs; stash the payload and drain on mount.
        self._pending_voice_frames: (
            tuple[dict[str, BuddyFrame], dict[str, dict[str, BuddyFrame]] | None]
            | None
        ) = None
        self._pending_chat_history: (
            list[tuple[float, str, str, str | None]] | None
        ) = None
        self._pending_status: str | None = None
        self._pending_voice_name: str | None = None

        self._voice_name: str = ""
        self._current_mood: str = "neutral"

        # Pending UI-thread dispatches queued before setup() ran.
        self._pending_post: list[Callable[[], None]] = []

    # --- Marshal helper -------------------------------------------------

    def _post(self, fn: Callable[[], None]) -> None:
        """Dispatch a no-arg callable to run on the UI thread. Safe to
        call from any thread.

        Pre-setup calls are buffered — the brain may fire adapter methods
        before ``setup()`` has built the widgets, and we'd rather replay
        those once the event loop is live than silently drop them or run
        them from the wrong thread.
        """
        if self._bridge is None:
            self._pending_post.append(fn)
            return
        self._bridge.dispatch.emit(fn)

    # --- Lifecycle ------------------------------------------------------

    def setup(self) -> None:
        self._app = ensure_qapplication()  # type: ignore[assignment]
        assert isinstance(self._app, QApplication)
        self._app.setQuitOnLastWindowClosed(False)

        # IMPORTANT: apply_macos_accessory_mode must run AFTER
        # ensure_qapplication — the NSApplication it pokes is the one
        # QApplication created underneath. Flipping the order
        # silently breaks Dock-hiding (the policy reverts when Qt
        # finally constructs its app).
        apply_macos_accessory_mode()
        warn_wayland_limitations()

        self._bridge = _UIBridge()

        self._buddy = BuddyWindow(
            frame_lines=BUDDY_IDLE,
            initial_anchor=(400.0, 200.0),
            font_family=self._font_family,
            font_size=self._font_size,
        )
        # Keep the speech bubble glued to the buddy as he swings — the
        # physics tick emits `position_changed` after every move.
        self._buddy.position_changed.connect(self._reposition_bubble)
        self._bubble = BubbleWidget(
            font_family=self._font_family,
            font_size=max(self._font_size - 1, 10),
        )
        self._chat = ChatWindow(
            on_submit=self._on_user_submit,
            buddy_name=self._buddy_name,
        )

        def _toggle_buddy() -> None:
            if self._buddy is None:
                return
            visible = self._buddy.isVisible()
            if visible:
                self._buddy.hide()
            else:
                self._buddy.show()
            if self._tray is not None:
                self._tray.set_buddy_visible(not visible)

        def _toggle_chat() -> None:
            # Funnel through _do_toggle_chat so the tray and the slash
            # command path share one implementation and the tray label
            # stays in sync with the window's actual visibility.
            self._do_toggle_chat()

        def _launch_options() -> None:
            # Route through the existing slash-command dispatcher in
            # app.py — it already knows how to assemble OptionsModalState
            # and call back into overlay.open_options_modal. Reusing it
            # keeps the tray and /options paths identical.
            cb = self._command_callback
            if cb is not None:
                cb("/options")

        def _quit() -> None:
            if self._app is not None:
                self._app.quit()

        self._tray = BuddyTrayIcon(
            on_toggle_buddy=_toggle_buddy,
            on_toggle_chat=_toggle_chat,
            on_options=_launch_options,
            on_quit=_quit,
        )
        self._buddy.set_right_click_handler(self._popup_tray_menu)

        self._hide_bubble_timer = QTimer(self._bridge)
        self._hide_bubble_timer.setSingleShot(True)
        self._hide_bubble_timer.timeout.connect(self._hide_bubble_now)

        # Replay any adapter calls that landed before we had widgets.
        for fn in self._pending_post:
            self._bridge.dispatch.emit(fn)
        self._pending_post.clear()
        self._drain_pending()

    def run_loop(self) -> None:
        if self._app is None:
            raise RuntimeError("QtOverlay.setup() must run before run_loop()")
        if self._buddy is not None:
            self._buddy.show()
            # NSWindow collectionBehavior can only be set once the
            # native window actually exists — i.e. after show().
            apply_macos_stay_visible(self._buddy)
        if self._chat is not None:
            # Show the chat window by default — otherwise the user has
            # no way to type to the buddy until they find the tray's
            # Show-chat action. The tray label stays in sync via
            # set_chat_visible so toggling from the menu still reads
            # correctly.
            self._chat.show()
            if self._tray is not None:
                self._tray.set_chat_visible(True)
        if self._tray is not None:
            self._tray.show()
        self._app.exec()

    def teardown(self) -> None:
        if self._hide_bubble_timer is not None:
            self._hide_bubble_timer.stop()
        if self._bubble is not None:
            self._bubble.hide()
            self._bubble.deleteLater()
        if self._chat is not None:
            self._chat.close()
            self._chat.deleteLater()
        if self._buddy is not None:
            self._buddy.close()
            self._buddy.deleteLater()
        if self._tray is not None:
            self._tray.hide()
        if self._app is not None:
            self._app.quit()

    def schedule_callback(
        self, callback: Callable[[], None], delay_ms: int = 0,
    ) -> None:
        if delay_ms <= 0:
            self._post(callback)
        else:
            # Marshal the QTimer call itself — QTimer lives on UI thread.
            self._post(lambda: QTimer.singleShot(delay_ms, callback))

    def request_exit(self) -> None:
        self._post(self.teardown)

    # --- Brain-invoked methods ------------------------------------------

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._post(lambda: self._do_set_frame(frame))

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._post(lambda: self._do_show_bubble(bubble))

    def hide_speech(self) -> None:
        self._post(self._hide_bubble_now)

    def update_status(self, text: str) -> None:
        self._pending_status = text
        self._post(lambda: self._do_update_status(text))

    def log_buddy_message(
        self, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        ts = time.time()
        speaker = self._voice_name or self._buddy_name
        self._post(lambda: self._do_log(ts, speaker, text, url, persist=True))

    def log_user_message(self, text: str) -> None:
        ts = time.time()
        self._post(lambda: self._do_log(ts, "you", text, None, persist=True))

    def clear_log(self) -> None:
        self._post(self._do_clear_chat)

    def load_chat_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        if self._chat is None:
            self._pending_chat_history = list(entries)
            return
        payload = list(entries)
        self._post(lambda: self._do_load_history(payload))

    def load_voice_frames(
        self,
        frames: dict[str, BuddyFrame],
        mood_frames: dict[str, dict[str, BuddyFrame]] | None = None,
    ) -> None:
        if self._buddy is None:
            self._pending_voice_frames = (frames, mood_frames)
            return
        # Phase 3: apply only the current-mood idle frame. Mood swapping
        # and voice-specific Rich markup translation land in Phase 4.
        idle = frames.get(self._current_mood) or frames.get("idle")
        if idle is not None:
            self._post(lambda: self._do_set_frame(idle))

    def clear_voice_frames(self) -> None:
        self._pending_voice_frames = None
        self._post(lambda: self._do_set_frame(BuddyFrame.get("idle")))

    def set_mood(self, mood: str) -> None:
        # TODO(phase4): re-render the buddy frame when mood changes
        # after voice frames have already been loaded.
        self._current_mood = mood

    def set_voice_name(self, name: str) -> None:
        self._voice_name = name
        if self._chat is None:
            self._pending_voice_name = name
            return
        self._post(lambda: self._do_set_voice_name(name))

    def _do_set_voice_name(self, name: str) -> None:
        if self._chat is not None:
            self._chat.setWindowTitle(f"{name or self._buddy_name} — chat")

    def toggle_chat_log(self) -> None:
        self._post(self._do_toggle_chat)

    # --- Modals ---------------------------------------------------------

    def open_selection_modal(
        self,
        title: str,
        groups: Any,
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> bool:
        group_list: list[SelectionGroup] = list(groups)
        self._post(lambda: self._do_open_selection_modal(
            title, group_list, on_save,
        ))
        return True

    def open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> bool:
        self._post(lambda: self._do_open_confirm_modal(title, body, on_result))
        return True

    def _do_open_selection_modal(
        self,
        title: str,
        groups: list[SelectionGroup],
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> None:
        dialog = SelectionDialog(title, groups, on_save, parent=self._chat)
        _focus_dialog(dialog)

    def _do_open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> None:
        dialog = ConfirmDialog(title, body, on_result, parent=self._chat)
        _focus_dialog(dialog)

    def open_options_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_options_modal(state, on_result))
        return True

    def _do_open_options_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        dialog = OptionsDialog(state, on_result, parent=self._chat)
        _focus_dialog(dialog)

    # --- Callback registration -------------------------------------------

    def set_input_callback(self, callback: Callable[[str], None]) -> None:
        self._input_callback = callback

    def set_command_callback(self, callback: Callable[[str], None]) -> None:
        self._command_callback = callback

    def set_buddy_reaction_callback(
        self, callback: Callable[[str], None],
    ) -> None:
        self._buddy_reaction_callback = callback

    def set_chat_persist_callback(
        self,
        persist: Callable[[str, str, str | None], None],
        clear: Callable[[], None],
    ) -> None:
        self._chat_persist_callback = persist
        self._chat_clear_callback = clear

    def set_environment_provider(
        self, provider: Callable[[], EnvironmentSnapshot] | None,
    ) -> None:
        # Phase 4 will add the particle overlay that consumes this; for
        # now just stash the reference so the wiring is in place.
        self._env_provider = provider

    # --- UI-thread implementations --------------------------------------

    def _do_set_frame(self, frame: BuddyFrame) -> None:
        if self._buddy is None:
            return
        self._buddy.set_frame(list(frame.lines))
        self._reposition_bubble()

    def _do_show_bubble(self, bubble: SpeechBubble) -> None:
        if self._bubble is None:
            return
        self._bubble.show_text(bubble.text, typing=not bubble.persistent)
        self._reposition_bubble()
        if self._hide_bubble_timer is not None and not bubble.persistent:
            self._hide_bubble_timer.start(_BUBBLE_HIDE_DELAY_MS)

    def _hide_bubble_now(self) -> None:
        if self._bubble is not None:
            self._bubble.hide_bubble()

    def _do_log(
        self,
        ts: float,
        author: str,
        text: str,
        url: str | None,
        *,
        persist: bool,
    ) -> None:
        if self._chat is not None:
            self._chat.append_line(ts, author, text, url)
        if persist and self._chat_persist_callback is not None:
            self._chat_persist_callback(author, text, url)

    def _do_clear_chat(self) -> None:
        if self._chat is not None:
            self._chat.clear_log()
        if self._chat_clear_callback is not None:
            self._chat_clear_callback()

    def _do_load_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        if self._chat is not None:
            self._chat.load_history(entries)

    def _do_update_status(self, text: str) -> None:
        if self._chat is not None:
            self._chat.set_status(text)

    def _do_toggle_chat(self) -> None:
        if self._chat is None:
            return
        if self._chat.isVisible():
            self._chat.hide()
            if self._tray is not None:
                self._tray.set_chat_visible(False)
        else:
            self._chat.show()
            self._chat.raise_()
            self._chat.focus_input()
            if self._tray is not None:
                self._tray.set_chat_visible(True)

    def _on_user_submit(self, text: str) -> None:
        # Called on the Qt main thread — safe to touch widgets.
        self._do_log(time.time(), "you", text, None, persist=True)
        if text.startswith("/") and self._command_callback is not None:
            self._command_callback(text)
        elif self._input_callback is not None:
            self._input_callback(text)

    def _reposition_bubble(self) -> None:
        if self._buddy is None or self._bubble is None:
            return
        geom = self._buddy.geometry()
        bubble_w = self._bubble.width()
        x = geom.x() + (geom.width() - bubble_w) // 2
        y = geom.y() - self._bubble.height() - _BUBBLE_HOVER_OFFSET_Y
        self._bubble.move(max(x, 0), max(y, 0))

    def _popup_tray_menu(self, global_pos: object) -> None:
        if self._tray is None:
            return
        menu = self._tray.contextMenu()
        if menu is not None:
            menu.popup(global_pos)  # type: ignore[arg-type]

    def _drain_pending(self) -> None:
        if self._pending_voice_frames is not None and self._buddy is not None:
            frames, mood_frames = self._pending_voice_frames
            self._pending_voice_frames = None
            self.load_voice_frames(frames, mood_frames)
        if self._pending_voice_name is not None and self._chat is not None:
            name = self._pending_voice_name
            self._pending_voice_name = None
            self._chat.setWindowTitle(f"{name or self._buddy_name} — chat")
        if self._pending_chat_history is not None and self._chat is not None:
            entries = self._pending_chat_history
            self._pending_chat_history = None
            self._chat.load_history(entries)
        if self._pending_status is not None and self._chat is not None:
            self._chat.set_status(self._pending_status)
            self._pending_status = None

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
from dataclasses import fields as dataclass_fields
from typing import Any, ClassVar

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from tokenpal.config.chatlog_writer import clamp_font_size
from tokenpal.config.schema import FontConfig
from tokenpal.ui.ascii_renderer import BUDDY_IDLE, BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.buddy_environment import EnvironmentSnapshot
from tokenpal.ui.qt import ensure_qapplication
from tokenpal.ui.qt._text_fx import qt_font_from_config
from tokenpal.ui.qt.buddy_window import BuddyWindow
from tokenpal.ui.qt.chat_window import ChatDock, ChatHistoryWindow
from tokenpal.ui.qt.cloud_dialog import CloudDialog
from tokenpal.ui.qt.modals import ConfirmDialog, SelectionDialog, _focus_dialog
from tokenpal.ui.qt.options_dialog import OptionsDialog
from tokenpal.ui.qt.platform import (
    apply_macos_accessory_mode,
    apply_macos_stay_visible,
    warn_wayland_limitations,
)
from tokenpal.ui.qt.speech_bubble import SpeechBubble as BubbleWidget
from tokenpal.ui.qt.tray import BuddyTrayIcon
from tokenpal.ui.qt.voice_dialog import VoiceDialog
from tokenpal.ui.registry import register_overlay
from tokenpal.ui.selection_modal import SelectionGroup

log = logging.getLogger(__name__)

_BUBBLE_HIDE_DELAY_MS = 6500  # how long a bubble lingers before auto-hide
_BUBBLE_HOVER_OFFSET_Y = 16    # px above the buddy window
_DOCK_OFFSET_Y = 4             # px below the buddy window's bottom edge
_CHAT_FONT_DEFAULT_SIZE = 13   # fallback + Ctrl+0 reset target


def _to_font_config(raw: Any) -> FontConfig:
    """Coerce a FontConfig, a dict, or ``None`` into a ``FontConfig``."""
    if isinstance(raw, FontConfig):
        return raw
    if isinstance(raw, dict):
        valid = {f.name for f in dataclass_fields(FontConfig)}
        return FontConfig(**{k: v for k, v in raw.items() if k in valid})
    return FontConfig()


def _default_monospace_family() -> str:
    """Pick a monospace family that actually exists on the host.

    "Courier" is missing on modern macOS — Qt substitutes silently but the
    substituted font's true advance can differ from fontMetrics, which
    breaks our wrap math. Prefer the platform-native monospace.
    """
    import platform

    system = platform.system()
    if system == "Darwin":
        return "Menlo"
    if system == "Windows":
        return "Consolas"
    return "DejaVu Sans Mono"


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
        # Empty string or missing key → platform default. Config schema
        # ships an empty default so we don't force "Courier" on macOS
        # (which Qt substitutes noisily — "Populating font family
        # aliases took 38 ms" warning on every launch).
        requested_family = config.get("font_family") or ""
        self._font_family: str = (
            requested_family or _default_monospace_family()
        )
        self._font_size: int = int(config.get("font_size", 14))

        # Per-surface font configs. The dataclass loader (issue #16) turns
        # [ui.chat_font] / [ui.bubble_font] into FontConfig instances;
        # dataclasses.asdict then flattens them to plain dicts before the
        # registry hands them off. Accept either shape for resilience.
        self._chat_font: FontConfig = _to_font_config(config.get("chat_font"))
        self._bubble_font: FontConfig = _to_font_config(config.get("bubble_font"))

        self._app: QApplication | None = None
        self._bridge: _UIBridge | None = None
        self._buddy: BuddyWindow | None = None
        self._bubble: BubbleWidget | None = None
        self._dock: ChatDock | None = None
        self._history: ChatHistoryWindow | None = None
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
        self._chat_font_persist_callback: (
            Callable[[FontConfig], None] | None
        ) = None

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
        self._last_status: str = ""

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
        # Keep the speech bubble + dock glued to the buddy as he swings
        # — the physics tick emits `position_changed` after every move.
        self._buddy.position_changed.connect(self._reposition_bubble)
        self._buddy.position_changed.connect(self._reposition_dock)
        self._bubble = BubbleWidget(
            font_family=self._font_family,
            font_size=max(self._font_size - 1, 10),
        )
        self._dock = ChatDock(
            on_submit=self._on_user_submit,
            on_zoom=self._handle_chat_zoom,
        )
        self._dock_docked: bool = False
        # User-intent visibility tracked separately from Qt's isVisible()
        # — macOS auto-hides frameless translucent windows on app
        # deactivate, but we only reparent on explicit user toggles.
        self._buddy_user_visible: bool = True
        self._history_user_visible: bool = False
        self._history = ChatHistoryWindow(
            buddy_name=self._buddy_name,
            on_hide=self._do_toggle_chat,
            on_zoom=self._handle_chat_zoom,
        )
        self._apply_chat_font_live()

        def _toggle_buddy() -> None:
            if self._buddy is None:
                return
            new_visible = not self._buddy_user_visible
            self._buddy_user_visible = new_visible
            if new_visible:
                self._buddy.show()
            else:
                self._buddy.hide()
            if self._tray is not None:
                self._tray.set_buddy_visible(new_visible)
            self._update_dock_placement()

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
        if self._dock is not None:
            # Position before show so it doesn't flash at (0, 0).
            self._reposition_dock()
            self._dock.show()
            apply_macos_stay_visible(self._dock)
            # The buddy's native window hasn't finished mapping on the
            # first show — geometry() reports stale pre-map values, so
            # the dock lands centered on the buddy instead of below him.
            # Re-run once the event loop has turned.
            QTimer.singleShot(0, self._reposition_dock)
        if self._history is not None:
            self._history.hide()
            if self._tray is not None:
                self._tray.set_chat_visible(False)
        if self._tray is not None:
            self._tray.show()
        self._app.exec()

    def teardown(self) -> None:
        if self._hide_bubble_timer is not None:
            self._hide_bubble_timer.stop()
        if self._bubble is not None:
            self._bubble.hide()
            self._bubble.deleteLater()
        if self._dock is not None:
            self._dock.close()
            self._dock.deleteLater()
        if self._history is not None:
            self._history.close()
            self._history.deleteLater()
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
        if self._history is None:
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
        if self._history is None:
            self._pending_voice_name = name
            return
        self._post(lambda: self._do_set_voice_name(name))

    def _do_set_voice_name(self, name: str) -> None:
        if self._history is not None:
            self._history.set_display_name(name or self._buddy_name)

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
        dialog = SelectionDialog(title, groups, on_save, parent=self._history)
        _focus_dialog(dialog)

    def _do_open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> None:
        dialog = ConfirmDialog(title, body, on_result, parent=self._history)
        _focus_dialog(dialog)

    def open_options_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_options_modal(state, on_result))
        return True

    def set_chat_history_opacity(self, opacity: float) -> None:
        def apply() -> None:
            if self._history is not None:
                self._history.set_background_opacity(opacity)
        self._post(apply)

    def set_chat_font(self, cfg: FontConfig) -> None:
        self._chat_font = cfg
        self._post(self._apply_chat_font_live)

    def set_bubble_font(self, cfg: FontConfig) -> None:
        self._bubble_font = cfg
        self._post(self._apply_bubble_font_live)

    def get_chat_font(self) -> FontConfig:
        return self._chat_font

    def get_bubble_font(self) -> FontConfig:
        return self._bubble_font

    def _apply_chat_font_live(self) -> None:
        font = qt_font_from_config(
            self._chat_font, fallback_size=_CHAT_FONT_DEFAULT_SIZE,
        )
        if self._dock is not None:
            self._dock.apply_font(font)
        if self._history is not None:
            self._history.apply_font(font)

    def _apply_bubble_font_live(self) -> None:
        if self._bubble is not None:
            self._bubble.apply_font_config(
                self._bubble_font,
                fallback_family=self._font_family,
                fallback_size=max(self._font_size - 1, 10),
            )

    def _handle_chat_zoom(self, delta: int) -> None:
        """``delta`` +1 / -1 bumps the chat font size; 0 resets to baseline."""
        current = self._chat_font
        current_size = (
            current.size_pt if current.size_pt > 0 else _CHAT_FONT_DEFAULT_SIZE
        )
        if delta == 0:
            new_size = _CHAT_FONT_DEFAULT_SIZE
        else:
            new_size = clamp_font_size(current_size + delta)
        if new_size == current_size and current.size_pt > 0:
            return
        self._chat_font = FontConfig(
            family=current.family,
            size_pt=new_size,
            bold=current.bold,
            italic=current.italic,
            underline=current.underline,
        )
        self._apply_chat_font_live()
        if self._chat_font_persist_callback is not None:
            self._chat_font_persist_callback(self._chat_font)

    def _do_open_options_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        dialog = OptionsDialog(
            state, on_result, parent=self._history,
            on_opacity_preview=self.set_chat_history_opacity,
        )
        _focus_dialog(dialog)

    def open_cloud_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_cloud_modal(state, on_result))
        return True

    def _do_open_cloud_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        dialog = CloudDialog(state, on_result, parent=self._history)
        _focus_dialog(dialog)

    def open_voice_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_voice_modal(state, on_result))
        return True

    def _do_open_voice_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        dialog = VoiceDialog(state, on_result, parent=self._history)
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

    def set_chat_font_persist_callback(
        self, persist: Callable[[FontConfig], None],
    ) -> None:
        self._chat_font_persist_callback = persist

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
        speaker = self._voice_name or self._buddy_name
        self._do_log(time.time(), speaker, bubble.text, None, persist=True)

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
        if self._history is not None:
            self._history.append_line(ts, author, text, url)
        if persist and self._chat_persist_callback is not None:
            self._chat_persist_callback(author, text, url)

    def _do_clear_chat(self) -> None:
        if self._history is not None:
            self._history.clear_log()
        if self._chat_clear_callback is not None:
            self._chat_clear_callback()

    def _do_load_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        if self._history is not None:
            self._history.load_history(entries)

    def _do_update_status(self, text: str) -> None:
        # Skip repaint + drop-shadow recomposite when the string
        # hasn't changed. _push_status fires on every orchestrator
        # state bump but many bumps produce the same composed string.
        if text == self._last_status:
            return
        self._last_status = text
        if self._dock is not None:
            self._dock.set_status(text)

    def _do_toggle_chat(self) -> None:
        if self._history is None:
            return
        new_visible = not self._history_user_visible
        self._history_user_visible = new_visible
        if new_visible:
            self._history.show()
            apply_macos_stay_visible(self._history)
            self._history.raise_()
        else:
            self._history.hide()
        if self._tray is not None:
            self._tray.set_chat_visible(new_visible)
        self._update_dock_placement()
        if new_visible and self._dock is not None:
            self._dock.focus_input()

    def _on_user_submit(self, text: str) -> None:
        # Called on the Qt main thread — safe to touch widgets.
        self._do_log(time.time(), "you", text, None, persist=True)
        if text.startswith("/") and self._command_callback is not None:
            self._command_callback(text)
        elif self._input_callback is not None:
            self._input_callback(text)

    def _update_dock_placement(self) -> None:
        """Reconcile dock placement with the current (buddy, history)
        visibility intent. Independent state — neither window toggles
        the other.

        buddy shown           → dock floats under buddy
        buddy hidden, hist on → dock embedded in history's bottom slot
        both hidden           → dock hidden (user must open a window)
        """
        if self._dock is None or self._history is None:
            return
        if self._buddy_user_visible:
            target = "floating"
        elif self._history_user_visible:
            target = "embedded"
        else:
            target = "hidden"
        self._apply_dock_mode(target)

    def _apply_dock_mode(self, mode: str) -> None:
        """Transition the dock to ``floating`` / ``embedded`` / ``hidden``."""
        if self._dock is None or self._history is None:
            return
        current = "embedded" if self._dock_docked else (
            "floating" if self._dock.isVisible() else "hidden"
        )
        if current == mode:
            return

        self._dock.hide()
        if self._dock_docked:
            self._history.release_dock(self._dock)
            self._dock_docked = False

        if mode == "hidden":
            return

        if mode == "embedded":
            self._dock.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, False,
            )
            self._dock.setWindowFlags(Qt.WindowType.Widget)
            self._history.embed_dock(self._dock)
            self._dock.show()
            self._dock_docked = True
            return

        # floating
        from tokenpal.ui.qt._text_fx import transparent_window_flags
        self._dock.setParent(None)
        self._dock.setWindowFlags(transparent_window_flags())
        self._dock.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True,
        )
        self._dock.restore_floating_size()
        self._reposition_dock()
        self._dock.show()
        apply_macos_stay_visible(self._dock)
        # Buddy's native window isn't fully mapped yet on show — re-run
        # reposition once the event loop has turned.
        QTimer.singleShot(0, self._reposition_dock)

    def _clamp_to_buddy_screen(
        self, x: int, y: int, w: int, h: int,
    ) -> tuple[int, int]:
        """Keep an (x, y, w, h) rect inside the buddy's current screen."""
        if self._buddy is None:
            return max(0, x), max(0, y)
        screen = self._buddy.screen()
        if screen is None:
            return max(0, x), max(0, y)
        avail = screen.availableGeometry()
        x = max(avail.left(), min(x, avail.right() - w + 1))
        y = max(avail.top(), min(y, avail.bottom() - h + 1))
        return x, y

    def _reposition_dock(self) -> None:
        """Anchor the floating input+status strip below the buddy.

        No-op when the dock is embedded in the history window — the
        history's layout owns positioning then.
        """
        if self._buddy is None or self._dock is None or self._dock_docked:
            return
        geom = self._buddy.geometry()
        w, h = self._dock.width(), self._dock.height()
        x = geom.x() + (geom.width() - w) // 2
        y = geom.bottom() + _DOCK_OFFSET_Y
        self._dock.move(*self._clamp_to_buddy_screen(x, y, w, h))

    def _reposition_bubble(self) -> None:
        if self._buddy is None or self._bubble is None:
            return
        geom = self._buddy.geometry()
        w, h = self._bubble.width(), self._bubble.height()
        x = geom.x() + (geom.width() - w) // 2
        y = geom.y() - h - _BUBBLE_HOVER_OFFSET_Y
        self._bubble.move(*self._clamp_to_buddy_screen(x, y, w, h))

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
        if self._pending_voice_name is not None and self._history is not None:
            name = self._pending_voice_name
            self._pending_voice_name = None
            self._do_set_voice_name(name)
        if (
            self._pending_chat_history is not None
            and self._history is not None
        ):
            entries = self._pending_chat_history
            self._pending_chat_history = None
            self._history.load_history(entries)
        if self._pending_status is not None and self._dock is not None:
            self._dock.set_status(self._pending_status)
            self._pending_status = None

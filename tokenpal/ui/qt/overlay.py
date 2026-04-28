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
import math
import time
from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from typing import Any, ClassVar

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QDialog

from tokenpal.brain.news_buffer import NewsItem
from tokenpal.config.chatlog_writer import clamp_font_size
from tokenpal.config.schema import FontConfig
from tokenpal.config.ui_state import UiState
from tokenpal.ui.ascii_renderer import BUDDY_IDLE, BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.buddy_environment import EnvironmentSnapshot
from tokenpal.ui.qt import ensure_qapplication
from tokenpal.ui.qt._chrome import BuddyResizeGrip
from tokenpal.ui.qt._log_window import TranslucentLogWindow
from tokenpal.ui.qt._text_fx import qt_font_from_config
from tokenpal.ui.qt.buddy_window import BuddyWindow
from tokenpal.ui.qt.chat_window import ChatDock, ChatHistoryWindow
from tokenpal.ui.qt.cloud_dialog import CloudDialog
from tokenpal.ui.qt.dock_mock import DockMock
from tokenpal.ui.qt.modals import ConfirmDialog, SelectionDialog, _focus_dialog
from tokenpal.ui.qt.news_window import NewsHistoryWindow
from tokenpal.ui.qt.options_dialog import OptionsDialog
from tokenpal.ui.qt.platform import (
    apply_macos_accessory_mode,
    apply_macos_click_through,
    apply_macos_stay_visible,
    lock_macos_child_above,
    warn_wayland_limitations,
)
from tokenpal.ui.qt.speech_bubble import SpeechBubble as BubbleWidget
from tokenpal.ui.qt.tray import BuddyTrayIcon, TrayWindow
from tokenpal.ui.qt.voice_dialog import VoiceDialog
from tokenpal.ui.qt.weather import (
    BuddyRainOverlay,
    SkyWindow,
    WeatherSim,
)
from tokenpal.ui.registry import register_overlay
from tokenpal.ui.selection_modal import SelectionGroup

log = logging.getLogger(__name__)

_BUBBLE_HIDE_DELAY_MS = 15000  # minimum lingers before auto-hide
# Estimated TTS pace at ~10 chars/sec (Kokoro). Used to extend bubble
# lifetime so it outlives audio playback for long replies — otherwise the
# bubble dismisses while the buddy is still talking and the mic is still
# muted, which reads as "frozen UI" to the user.
_BUBBLE_TTS_MS_PER_CHAR = 100
_BUBBLE_TTS_PADDING_MS = 2000
_BUBBLE_HOVER_OFFSET_Y = 16    # px above the buddy window
_DOCK_OFFSET_Y = 4             # px below the buddy window's bottom edge
_CHAT_FONT_DEFAULT_SIZE = 13   # fallback + Ctrl+0 reset target
# Park position for the real ``ChatDock`` while the swing-mock is up.
# Off-screen but alive — hiding the NSWindow instead was breaking the
# QLineEdit focus chain on macOS. Windows clamps window coords at
# ±32767; stay well inside that to avoid QWindowsWindow warnings.
_DOCK_PARK_X = -10000
_DOCK_PARK_Y = -10000

_ZOOM_MIN = 0.5
_ZOOM_MAX = 2.5
# Drag-zoom sensitivity: vertical pixels of grip-drag → zoom delta.
# 0.005 makes a 200 px drag = 1.0× zoom delta, fitting the 0.5–2.5
# range comfortably in a single drag without feeling twitchy.
_ZOOM_PER_DRAG_PX = 0.005
# Float-precision floor — drag arithmetic produces non-canonical
# floats; snap to 4dp so identical-looking factors short-circuit.
_ZOOM_PRECISION_DP = 4
# Coalesce persist requests so a 60 Hz drag-zoom doesn't hammer
# ``save_ui_state`` (synchronous JSON write + chmod on Linux) on the
# Qt main thread. Far enough out to debounce a continuous drag, short
# enough that the user still sees state survive an immediate quit.
_PERSIST_DEBOUNCE_MS = 250

# Stable registry keys for the overlay's toggleable log windows.
# These names go to disk via UiState, so don't rename without a
# migration step.
_WINDOW_CHAT = "chat"
_WINDOW_NEWS = "news"


def _clamp_zoom(factor: float) -> float:
    """Clamp ``factor`` into [_ZOOM_MIN, _ZOOM_MAX] and snap to 4dp so
    drag-arithmetic noise doesn't churn the persist pipeline."""
    return round(max(_ZOOM_MIN, min(_ZOOM_MAX, factor)), _ZOOM_PRECISION_DP)


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
        # Registry of toggleable log windows. Source of truth for
        # iteration (run_loop show/hide, teardown, persist, font/color
        # apply). The named refs ``_history`` / ``_news`` below point at
        # the same instances and are kept for typed call sites that
        # need subclass-specific methods (``embed_dock``, ``append_items``).
        self._log_windows: dict[str, TranslucentLogWindow] = {}
        self._user_visible: dict[str, bool] = {}
        self._history: ChatHistoryWindow | None = None
        self._news: NewsHistoryWindow | None = None
        self._tray: BuddyTrayIcon | None = None
        self._weather_sim: WeatherSim | None = None
        self._sky_window: SkyWindow | None = None
        self._buddy_rain_overlay: BuddyRainOverlay | None = None
        self._resize_grip: BuddyResizeGrip | None = None
        # Live non-modal dialog instances — keep one of each on screen at
        # a time; repeat slash commands focus the existing window.
        self._options_dialog: OptionsDialog | None = None
        self._cloud_dialog: CloudDialog | None = None
        self._voice_dialog: VoiceDialog | None = None
        self._selection_dialog: SelectionDialog | None = None
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
        self._ui_state_persist_callback: (
            Callable[[UiState], None] | None
        ) = None
        self._zoom: float = 1.0
        self._persist_ui_state_timer: QTimer | None = None
        self._persist_pending: bool = False

        # Pre-setup buffers. The brain may call any adapter method before
        # setup() runs; stash the payload and drain on mount.
        self._pending_voice_frames: (
            tuple[dict[str, BuddyFrame], dict[str, dict[str, BuddyFrame]] | None]
            | None
        ) = None
        self._pending_chat_history: (
            list[tuple[float, str, str, str | None]] | None
        ) = None
        self._pending_news_items: list[NewsItem] | None = None
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
        # Painted stand-in used while the buddy is mid-swing. The real
        # dock is hidden and this mock shows a rotated snapshot. See
        # ``_reposition_dock`` for the swap logic.
        self._dock_mock = DockMock()
        self._dock_mock_active = False
        self._dock_docked: bool = False
        # User-intent visibility tracked separately from Qt's isVisible()
        # — macOS auto-hides frameless translucent windows on app
        # deactivate, but we only reparent on explicit user toggles.
        self._buddy_user_visible: bool = True
        self._history = ChatHistoryWindow(
            buddy_name=self._buddy_name,
            on_hide=self._do_toggle_chat,
            on_zoom=self._handle_chat_zoom,
        )
        self._news = NewsHistoryWindow(
            title=f"{self._buddy_name} — news",
            on_hide=lambda: self._do_toggle_window(_WINDOW_NEWS),
            on_zoom=self._handle_chat_zoom,
        )
        # Register both windows. The chat window keeps its own typed
        # toggle path (``_do_toggle_chat``) because it has to drive the
        # dock-embed + focus-input sequence after toggling; the news
        # window goes through the generic ``_do_toggle_window``.
        # Keep any visibility intent already restored from disk.
        self._log_windows[_WINDOW_CHAT] = self._history
        self._log_windows[_WINDOW_NEWS] = self._news
        self._user_visible.setdefault(_WINDOW_CHAT, False)
        self._user_visible.setdefault(_WINDOW_NEWS, False)
        self._apply_chat_font_live()

        # Weather overlay — sky + buddy-rain overlay + shared sim. The
        # sim reads the already-wired ``_env_provider`` and the buddy's
        # rotated art rect. ``SkyWindow`` owns the 30 Hz tick; we wire
        # the rain overlay to re-anchor on every ``position_changed``.
        self._weather_sim = WeatherSim(
            env_provider=self._env_provider_for_sim,
            buddy_rect_provider=self._buddy_world_rect_for_sim,
            buddy_art_hit=self._buddy_art_hit_for_sim,
            cell_px=10.0,
        )
        self._buddy_rain_overlay = BuddyRainOverlay(
            self._weather_sim,
            font_family=self._font_family,
            font_size=max(self._font_size - 2, 8),
            buddy_rect_provider=self._buddy_world_rect_for_sim,
        )
        self._sky_window = SkyWindow(
            self._weather_sim,
            font_family=self._font_family,
            font_size=self._font_size,
            overlay_update_hook=self._buddy_rain_overlay.update,
            buddy_rect_provider=self._buddy_world_rect_for_sim,
        )
        self._buddy.position_changed.connect(self._reanchor_weather)

        # Top-level resize grip — pure paint, rotates via paintEvent
        # like the speech bubble. Anchored to the buddy's body-frame
        # bottom-right via _reposition_grip.
        self._resize_grip = BuddyResizeGrip()
        self._resize_grip.zoom_drag_delta.connect(self._on_zoom_drag_delta)
        self._buddy.position_changed.connect(self._reposition_grip)

        # Apply any zoom restored from disk so initial layout uses the
        # persisted scale instead of 1.0×. ``_fan_out_zoom`` skips the
        # equality guard in ``set_zoom`` and the persist round-trip.
        if self._zoom != 1.0:
            self._fan_out_zoom(self._zoom)

        def _toggle_buddy() -> None:
            if self._buddy is None:
                return
            new_visible = not self._buddy_user_visible
            self._buddy_user_visible = new_visible
            if new_visible:
                self._buddy.show()
                if self._resize_grip is not None:
                    self._resize_grip.show()
                    lock_macos_child_above(self._buddy, self._resize_grip)
                    self._reposition_grip()
                if self._sky_window is not None:
                    self._sky_window.show()
                    apply_macos_stay_visible(self._sky_window)
                    apply_macos_click_through(self._sky_window)
                    self._sky_window.reanchor()
                if self._buddy_rain_overlay is not None:
                    self._buddy_rain_overlay.show()
                    apply_macos_click_through(self._buddy_rain_overlay)
                    self._buddy_rain_overlay.reanchor()
            else:
                self._buddy.hide()
                if self._resize_grip is not None:
                    self._resize_grip.hide()
                # A bubble already painted on screen would linger as a
                # detached top-level window after the buddy vanishes.
                if self._hide_bubble_timer is not None:
                    self._hide_bubble_timer.stop()
                if self._bubble is not None:
                    self._bubble.hide_bubble()
                if self._sky_window is not None:
                    self._sky_window.hide()
                if self._buddy_rain_overlay is not None:
                    self._buddy_rain_overlay.hide()
            if self._tray is not None:
                self._tray.set_buddy_visible(new_visible)
            self._update_dock_placement()
            # When buddy hides while history is already open the dock
            # reparents into the history window. The history NSWindow
            # must be activated or the embedded QLineEdit can't receive
            # key events.
            if (
                not new_visible
                and self._user_visible.get(_WINDOW_CHAT, False)
                and self._history is not None
            ):
                self._history.activateWindow()
                if self._dock is not None:
                    self._dock.focus_input()
            self._persist_ui_state()

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
            windows=[
                TrayWindow(
                    name=_WINDOW_CHAT,
                    show_label="Show chat log",
                    hide_label="Hide chat log",
                    on_toggle=self._do_toggle_chat,
                ),
                TrayWindow(
                    name=_WINDOW_NEWS,
                    show_label="Show news",
                    hide_label="Hide news",
                    on_toggle=lambda: self._do_toggle_window(_WINDOW_NEWS),
                ),
            ],
            on_options=_launch_options,
            on_quit=_quit,
        )
        self._buddy.set_right_click_handler(self._popup_tray_menu)

        self._hide_bubble_timer = QTimer(self._bridge)
        self._hide_bubble_timer.setSingleShot(True)
        self._hide_bubble_timer.timeout.connect(self._hide_bubble_now)
        self._bubble_stay_visible_applied = False

        # Replay any adapter calls that landed before we had widgets.
        for fn in self._pending_post:
            self._bridge.dispatch.emit(fn)
        self._pending_post.clear()
        self._drain_pending()

    def run_loop(self) -> None:
        if self._app is None:
            raise RuntimeError("QtOverlay.setup() must run before run_loop()")
        if self._buddy is not None and self._buddy_user_visible:
            self._buddy.show()
            # NSWindow collectionBehavior can only be set once the
            # native window actually exists: after show().
            apply_macos_stay_visible(self._buddy)
            if self._resize_grip is not None:
                self._resize_grip.show()
                apply_macos_stay_visible(self._resize_grip)
                lock_macos_child_above(self._buddy, self._resize_grip)
                self._reposition_grip()
        if self._sky_window is not None and self._buddy_user_visible:
            self._sky_window.show()
            apply_macos_stay_visible(self._sky_window)
            # NSWindow.setIgnoresMouseEvents is the native click-through
            # toggle — Qt's ``WA_TransparentForMouseEvents`` alone isn't
            # enough on macOS; the NSWindow still swallows clicks.
            apply_macos_click_through(self._sky_window)
            self._sky_window.start()
            # The buddy's ``position_changed`` signal only fires while the
            # physics tick is running. A stationary buddy never emits
            # after show, so the sky's own __init__ reanchor ran against
            # a pre-mapped buddy rect. Force one now (and once later so
            # the native window has finished sizing on macOS).
            self._sky_window.reanchor()
            if self._buddy_rain_overlay is not None:
                self._buddy_rain_overlay.show()
                apply_macos_click_through(self._buddy_rain_overlay)
                self._buddy_rain_overlay.reanchor()
            QTimer.singleShot(150, self._reanchor_weather)
        for name, window in self._log_windows.items():
            if self._user_visible.get(name, False):
                window.show()
                apply_macos_stay_visible(window)
                window.raise_()
                window.activateWindow()
            else:
                window.hide()
        if self._dock is not None:
            # Pre-position so the dock doesn't flash at (0, 0) when
            # _update_dock_placement decides to float it.
            self._reposition_dock()
            # The buddy's native window hasn't finished mapping on the
            # first show, so geometry() reports stale pre-map values;
            # re-run once the event loop has turned, and again after a
            # short delay to cover the case where voice frames have
            # just been loaded and the resulting widget resize hasn't
            # propagated to ``pos()`` yet.
            QTimer.singleShot(0, self._reposition_dock)
            QTimer.singleShot(150, self._reposition_dock)
        # _update_dock_placement handles the dock's float/embed/hide
        # transition based on the restored visibility flags.
        self._update_dock_placement()
        if self._tray is not None:
            self._tray.set_buddy_visible(self._buddy_user_visible)
            for name in self._log_windows:
                self._tray.set_window_visible(
                    name, self._user_visible.get(name, False),
                )
            self._tray.show()
        self._app.exec()

    def teardown(self) -> None:
        self.flush_pending_persist()
        if self._hide_bubble_timer is not None:
            self._hide_bubble_timer.stop()
        # Weather teardown order: stop the tick BEFORE deleteLater so the
        # timer can't fire a callback into a half-deleted widget.
        if self._sky_window is not None:
            self._sky_window.stop()
            self._sky_window.hide()
            self._sky_window.deleteLater()
        if self._buddy_rain_overlay is not None:
            self._buddy_rain_overlay.hide()
            self._buddy_rain_overlay.deleteLater()
        if self._bubble is not None:
            self._bubble.hide()
            self._bubble.deleteLater()
        if self._dock is not None:
            self._dock.close()
            self._dock.deleteLater()
        for window in self._log_windows.values():
            window.close()
            window.deleteLater()
        if self._resize_grip is not None:
            self._resize_grip.close()
            self._resize_grip.deleteLater()
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

    def _open_singleton_dialog(
        self, attr: str, factory: Callable[[], QDialog],
    ) -> None:
        """Focus the existing dialog held at ``self.<attr>`` if one is
        already on screen; otherwise build one via ``factory``, wire it
        up to clear ``self.<attr>`` on close, and focus it."""
        existing: QDialog | None = getattr(self, attr)
        if existing is not None:
            _focus_dialog(existing)
            return
        dialog = factory()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # ``finished`` fires synchronously inside accept()/reject() before
        # the deferred-delete event, so the ref is clear in time for a
        # rapid reopen in the same event-loop tick.
        dialog.finished.connect(lambda _=0: setattr(self, attr, None))
        setattr(self, attr, dialog)
        _focus_dialog(dialog)

    def _do_open_selection_modal(
        self,
        title: str,
        groups: list[SelectionGroup],
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> None:
        self._open_singleton_dialog(
            "_selection_dialog",
            lambda: SelectionDialog(
                title, groups, on_save, parent=self._history,
            ),
        )

    def _do_open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> None:
        dialog = ConfirmDialog(title, body, on_result, parent=self._history)
        _focus_dialog(dialog)

    def open_options_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
        on_open_subdialog: Callable[[str], None] | None = None,
    ) -> bool:
        self._post(lambda: self._do_open_options_modal(
            state, on_result, on_open_subdialog,
        ))
        return True

    def set_chat_history_opacity(self, opacity: float) -> None:
        def apply() -> None:
            for window in self._log_windows.values():
                window.set_background_opacity(opacity)
        self._post(apply)

    def set_chat_history_background_color(self, hex_color: str) -> None:
        def apply() -> None:
            for window in self._log_windows.values():
                window.set_background_color(hex_color)
            if self._bubble is not None:
                self._bubble.set_background_color(hex_color)
        self._post(apply)

    def set_chat_history_font_color(self, hex_color: str) -> None:
        def apply() -> None:
            for window in self._log_windows.values():
                window.set_font_color(hex_color)
            if self._bubble is not None:
                self._bubble.set_font_color(hex_color)
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
        for window in self._log_windows.values():
            window.apply_font(font)

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
        self,
        state: Any,
        on_result: Callable[[Any], None],
        on_open_subdialog: Callable[[str], None] | None,
    ) -> None:
        self._open_singleton_dialog(
            "_options_dialog",
            lambda: OptionsDialog(
                state, on_result, parent=self._history,
                on_opacity_preview=self.set_chat_history_opacity,
                on_background_color_preview=(
                    self.set_chat_history_background_color
                ),
                on_font_color_preview=self.set_chat_history_font_color,
                on_open_subdialog=on_open_subdialog,
            ),
        )

    def open_cloud_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_cloud_modal(state, on_result))
        return True

    def _do_open_cloud_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        self._open_singleton_dialog(
            "_cloud_dialog",
            lambda: CloudDialog(state, on_result, parent=self._history),
        )

    def open_voice_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> bool:
        self._post(lambda: self._do_open_voice_modal(state, on_result))
        return True

    def _do_open_voice_modal(
        self, state: Any, on_result: Callable[[Any], None],
    ) -> None:
        self._open_singleton_dialog(
            "_voice_dialog",
            lambda: VoiceDialog(state, on_result, parent=self._history),
        )

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

    def set_ui_state_persist_callback(
        self, persist: Callable[[UiState], None],
    ) -> None:
        """Register a callback invoked on every visibility toggle or
        zoom change. Receives the full ``UiState`` dict so future
        fields don't need to thread through this signature.

        Runs synchronously on the Qt main thread; app.py uses it to
        write a small JSON file.
        """
        self._ui_state_persist_callback = persist

    def restore_visibility_state(
        self,
        *,
        buddy_visible: bool,
        windows: dict[str, bool] | None = None,
        zoom: float | None = None,
    ) -> None:
        """Override the defaults before ``run_loop`` decides what to show.

        ``windows`` is a ``{name: visible}`` map matching the registry
        keys. Unknown keys are ignored; missing keys keep their default.
        Safe to call before ``setup()`` populates the registry — the
        intent is stashed and consumed via ``setdefault`` at register time.

        ``zoom`` is stashed and applied to the buddy/bubble/dock/sky/rain
        on first ``setup()``; clamped to the same range as ``set_zoom``.
        """
        self._buddy_user_visible = bool(buddy_visible)
        if windows is not None:
            for name, visible in windows.items():
                self._user_visible[name] = bool(visible)
        if zoom is not None:
            self._zoom = _clamp_zoom(zoom)

    def set_zoom(self, factor: float) -> None:
        """Wholesale-rescale buddy + bubble + dock + sky + rain by
        ``factor``. Clamped to [0.5, 2.5] and snapped to 4dp. No-op if
        the clamped factor matches the current zoom. Persists to disk
        on every change so a freshly-zoomed buddy survives a restart."""
        clamped = _clamp_zoom(factor)
        if clamped == self._zoom:
            return
        self._fan_out_zoom(clamped)
        self._persist_ui_state()

    def _fan_out_zoom(self, factor: float) -> None:
        """Apply ``factor`` to every owned widget and reanchor followers.
        Bypasses the noop guard + persist in ``set_zoom`` — used by
        ``set_zoom`` itself and by setup-time restore where the guard
        would skip the work."""
        self._zoom = factor
        if self._buddy is not None:
            self._buddy.set_zoom(factor)
        if self._bubble is not None:
            self._bubble.set_zoom(factor)
        if self._dock is not None:
            self._dock.set_zoom(factor)
        if self._sky_window is not None:
            self._sky_window.set_zoom(factor)
        if self._buddy_rain_overlay is not None:
            self._buddy_rain_overlay.set_zoom(factor)
        self._reanchor_weather()
        self._reposition_bubble()
        self._reposition_dock()

    def _on_zoom_drag_delta(self, dy: int) -> None:
        """Slot for ``BuddyResizeGrip.zoom_drag_delta``. Drag-down grows
        the buddy, drag-up shrinks (matches the SizeFDiagCursor
        convention used by the corner grip)."""
        self.set_zoom(self._zoom + dy * _ZOOM_PER_DRAG_PX)

    def _persist_ui_state(self) -> None:
        """Coalesced persist: schedules a single write ~250 ms after
        the last call, so a 60 Hz drag-zoom doesn't pin the Qt main
        thread on synchronous JSON writes + chmod."""
        if self._ui_state_persist_callback is None:
            return
        self._persist_pending = True
        timer = self._persist_ui_state_timer
        if timer is None:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(self._flush_ui_state)
            self._persist_ui_state_timer = timer
        timer.start(_PERSIST_DEBOUNCE_MS)

    def flush_pending_persist(self) -> None:
        """Force any debounced persist to fire immediately. Used by
        ``teardown`` so a final drag-zoom isn't lost on shutdown, and
        by tests that assert the callback fired. No-op if no write is
        pending."""
        if not self._persist_pending:
            return
        timer = self._persist_ui_state_timer
        if timer is not None and timer.isActive():
            timer.stop()
        self._flush_ui_state()

    def _flush_ui_state(self) -> None:
        self._persist_pending = False
        cb = self._ui_state_persist_callback
        if cb is None:
            return
        state: UiState = {
            "buddy_visible": self._buddy_user_visible,
            "windows": {name: bool(v) for name, v in self._user_visible.items()},
            "zoom": self._zoom,
        }
        try:
            cb(state)
        except Exception:
            log.exception("ui_state persist callback failed")

    def set_environment_provider(
        self, provider: Callable[[], EnvironmentSnapshot] | None,
    ) -> None:
        self._env_provider = provider

    def force_weather(
        self,
        *,
        weather_code: int | None = None,
        hour: int | None = None,
        clear: bool = False,
    ) -> None:
        """Dev override for the /weather slash command — pipes through to
        ``WeatherSim.set_override`` on the Qt thread."""
        def apply() -> None:
            if self._weather_sim is None:
                return
            self._weather_sim.set_override(
                weather_code=weather_code, hour=hour, clear=clear,
            )
        self._post(apply)

    def _env_provider_for_sim(self) -> EnvironmentSnapshot | None:
        """Weather sim calls this from the Qt thread at 30 Hz. Guards
        against a pre-mount call (provider not yet set) and exceptions
        from the brain so a transient error can't kill the Qt tick."""
        prov = self._env_provider
        if prov is None:
            return None
        try:
            return prov()
        except Exception:
            log.exception("env provider raised — suppressing for this tick")
            return None

    def _buddy_world_rect_for_sim(self) -> QRectF | None:
        if self._buddy is None or not self._buddy_user_visible:
            return None
        r = self._buddy.buddy_occlusion_rect_world()
        if r.width() <= 0 or r.height() <= 0:
            return None
        return QRectF(r)

    def _buddy_art_hit_for_sim(self, world_point: QPointF) -> bool:
        """Per-cell art hit-test. The sim already passed the coarse
        world-rect filter; here we invert the buddy's paint transform
        and check whether the point lands on an actually-painted glyph
        (not just the AABB) so drops fall through gaps in the art."""
        if self._buddy is None:
            return False
        art_point = self._buddy.world_to_art(world_point)
        if art_point is None:
            return False
        return self._buddy.is_painted_cell_at(art_point.x(), art_point.y())

    def _reanchor_weather(self) -> None:
        """Slot for ``BuddyWindow.position_changed``. Re-anchor is O(1)
        and idempotent (see each widget's ``reanchor``), safe to call
        from a 60 Hz signal. Also drop accumulated shoulder-snow if the
        buddy is being dragged so the dust doesn't float mid-air.

        The rain overlay's paint is coupled to motion here (not just to
        the 30 Hz sky tick). Without this, ``self.move()`` runs every
        tick but ``paintEvent`` only runs at 30 Hz — between paints the
        existing backbuffer slides under the buddy at the new window
        position, producing the rotating-shadow artefact. Sky tick still
        triggers ``update()`` so particles keep animating while the
        buddy is asleep."""
        if self._sky_window is not None:
            self._sky_window.reanchor()
        if self._buddy_rain_overlay is None:
            return
        self._buddy_rain_overlay.reanchor()
        self._buddy_rain_overlay.update()
        if (
            self._buddy is not None
            and self._weather_sim is not None
            and self._buddy.is_dragging()
        ):
            self._weather_sim.clear_buddy_accum()

    # --- UI-thread implementations --------------------------------------

    def _do_set_frame(self, frame: BuddyFrame) -> None:
        if self._buddy is None:
            return
        self._buddy.set_frame(list(frame.lines))
        self._reposition_bubble()
        # Voice frames may change the art bounding box (→ widget resize
        # → new foot_world_position). The buddy's position_changed
        # signal covers most of the anchor churn, but on macOS
        # ``pos()`` can lag a tick right after a resize, so also push
        # the update directly here.
        self._reposition_dock()

    def _do_show_bubble(self, bubble: SpeechBubble) -> None:
        if self._bubble is None:
            return
        speaker = self._voice_name or self._buddy_name
        # Chat log still records the utterance even when the buddy
        # window is hidden: users who only have the chat log open
        # should still see what the buddy said. Only the on-screen
        # bubble is gated on buddy visibility.
        if self._buddy_user_visible:
            self._bubble.show_text(bubble.text, typing=not bubble.persistent)
            # Same NSWindow collectionBehavior treatment the buddy gets:
            # the native window only exists after the first show(), so we
            # defer this past construction. One-shot — collectionBehavior
            # persists across hide/show.
            if not self._bubble_stay_visible_applied:
                apply_macos_stay_visible(self._bubble)
                self._bubble_stay_visible_applied = True
            self._reposition_bubble()
            if self._hide_bubble_timer is not None and not bubble.persistent:
                tts_ms = len(bubble.text) * _BUBBLE_TTS_MS_PER_CHAR + _BUBBLE_TTS_PADDING_MS
                self._hide_bubble_timer.start(max(_BUBBLE_HIDE_DELAY_MS, tts_ms))
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

    def _do_toggle_window(self, name: str) -> None:
        """Generic show/hide for a registered log window. Window-
        specific post-toggle work (dock embed, focus_input) lives in
        callers like ``_do_toggle_chat`` and reads back ``_user_visible``."""
        window = self._log_windows.get(name)
        if window is None:
            return
        new_visible = not self._user_visible.get(name, False)
        self._user_visible[name] = new_visible
        if new_visible:
            window.show()
            apply_macos_stay_visible(window)
            window.raise_()
            window.activateWindow()
        else:
            window.hide()
        if self._tray is not None:
            self._tray.set_window_visible(name, new_visible)
        self._persist_ui_state()

    def _do_toggle_chat(self) -> None:
        self._do_toggle_window(_WINDOW_CHAT)
        # Chat owns the dock-embedding handshake when buddy is hidden;
        # the generic toggle can't know about that.
        self._update_dock_placement()
        if self._user_visible.get(_WINDOW_CHAT, False) and self._dock is not None:
            self._dock.focus_input()

    def add_news_items(self, items: list[NewsItem]) -> None:
        if not items:
            return
        if self._news is None:
            if self._pending_news_items is None:
                self._pending_news_items = []
            self._pending_news_items.extend(items)
            return
        payload = list(items)
        self._post(lambda: self._do_add_news_items(payload))

    def toggle_news_history(self) -> None:
        self._post(lambda: self._do_toggle_window(_WINDOW_NEWS))

    def _do_add_news_items(self, items: list[NewsItem]) -> None:
        if self._news is not None:
            self._news.append_items(items)

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
        elif self._user_visible.get(_WINDOW_CHAT, False):
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
            # The floating dock sets WA_ShowWithoutActivating so clicking
            # the pill never activates the app. Once embedded in the
            # history window, we *do* want activation: otherwise the
            # frameless NSWindow that wraps the history never becomes key
            # and the QLineEdit silently drops every keystroke.
            self._dock.setAttribute(
                Qt.WidgetAttribute.WA_ShowWithoutActivating, False,
            )
            self._dock.setWindowFlags(Qt.WindowType.Widget)
            self._history.embed_dock(self._dock)
            self._dock.show()
            self._dock_docked = True
            self._history.activateWindow()
            self._dock.focus_input()
            return

        # floating
        from tokenpal.ui.qt._text_fx import transparent_window_flags
        self._dock.setParent(None)
        self._dock.setWindowFlags(transparent_window_flags())
        self._dock.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True,
        )
        self._dock.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating, True,
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

    def _body_aligned_offset(
        self, angle: float, dx: float, dy: float,
    ) -> tuple[float, float]:
        """Rotate an art-frame offset ``(dx, dy)`` by ``angle`` so the
        follower trails the body's pose instead of drifting in screen
        axes as the buddy tilts. θ=0 is upright; positive θ rotates the
        +y axis toward the left in screen coords (matches the physics
        sim's convention)."""
        s = math.sin(angle)
        c = math.cos(angle)
        return (dx * c - dy * s, dx * s + dy * c)

    def _reposition_dock(self) -> None:
        """Anchor the input+status strip below the buddy's feet.

        While the body is upright, the real ``ChatDock`` tracks the
        rotated foot position. While the body is rotating, the mock is
        painted on top at the same anchor and the real dock is parked
        off-screen. Parking (rather than hiding) keeps the NSWindow
        alive and its activation chain intact, so the ``QLineEdit``
        cleanly reclaims focus once the mock goes away.

        No-op when the dock is embedded in the history window — the
        history's layout owns positioning then.
        """
        if self._buddy is None or self._dock is None or self._dock_docked:
            return
        angle = self._buddy.body_angle()
        foot = self._buddy.foot_world_position()
        ox, oy = self._body_aligned_offset(angle, 0.0, float(_DOCK_OFFSET_Y))
        anchor_x = foot.x() + ox
        anchor_y = foot.y() + oy
        w, h = self._dock.width(), self._dock.height()

        if self._buddy.needs_rotated_followers():
            if not self._dock_mock_active:
                self._dock_mock.set_source(self._dock.grab())
                self._dock.move(_DOCK_PARK_X, _DOCK_PARK_Y)
                self._dock_mock.show()
                self._dock_mock_active = True
            self._dock_mock.set_pose(QPointF(anchor_x, anchor_y), angle)
        else:
            if self._dock_mock_active:
                self._dock_mock.hide()
                self._dock_mock_active = False
            x = int(anchor_x) - w // 2
            y = int(anchor_y)
            self._dock.move(*self._clamp_to_buddy_screen(x, y, w, h))

    def _reposition_grip(self) -> None:
        """Anchor the resize grip to the buddy's body-frame bottom-right
        corner. The grip is a top-level pure-paint widget; rotating it
        with the buddy gives the same visual fake-out the bubble uses
        without the snapshot-and-park trick the dock needs."""
        if self._buddy is None or self._resize_grip is None:
            return
        bounds = self._buddy.art_bounds()
        corner = self._buddy.art_frame_point_world(
            float(bounds.width()), float(bounds.height()),
        )
        self._resize_grip.set_pose(corner, self._buddy.body_angle())

    def _reposition_bubble(self) -> None:
        """Anchor the speech bubble above the buddy's rotated head.

        The bubble rotates with the body: its tail (bottom-center)
        stays glued to the head-plus-hover-offset point in world
        coords, and the bubble content itself rotates by ``body_angle``
        so it swings naturally with him.
        """
        if self._buddy is None or self._bubble is None:
            return
        angle = self._buddy.body_angle()
        head = self._buddy.head_world_position()
        ox, oy = self._body_aligned_offset(
            angle, 0.0, -float(_BUBBLE_HOVER_OFFSET_Y),
        )
        self._bubble.set_pose(QPointF(head.x() + ox, head.y() + oy), angle)

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
        if self._pending_news_items is not None and self._news is not None:
            items = self._pending_news_items
            self._pending_news_items = None
            self._news.append_items(items)
        if self._pending_status is not None and self._dock is not None:
            self._dock.set_status(self._pending_status)
            self._pending_status = None

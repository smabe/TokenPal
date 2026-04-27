"""Chat surfaces for the Qt frontend: ``ChatDock`` (always-visible
input + status strip anchored under the buddy) and
``ChatHistoryWindow`` (standalone scrollable log, starts hidden).
Shared chrome lives in ``qt/_chrome.py`` and ``qt/_log_window.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tokenpal.ui.chat_format import format_chat_ts
from tokenpal.ui.qt._chrome import install_zoom_shortcuts
from tokenpal.ui.qt._log_window import TranslucentLogWindow
from tokenpal.ui.qt._text_fx import (
    apply_drop_shadow,
    glass_pill_stylesheet,
    scale_font,
    transparent_window_flags,
)

_DOCK_DEFAULT_WIDTH = 360
_DOCK_INPUT_HEIGHT = 32
_DOCK_STATUS_ROW_HEIGHT = 26
_HISTORY_DEFAULT_SIZE = (520, 380)


class ChatDock(QWidget):
    """Frameless translucent input + status strip.

    Anchored under the buddy by ``QtOverlay._reposition_dock``. Emits
    user input through the registered ``on_submit`` callback exactly
    like the old monolithic ``ChatWindow`` did.
    """

    def __init__(
        self,
        *,
        on_submit: Callable[[str], None] | None = None,
        on_zoom: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_submit = on_submit
        self._zoom = 1.0
        self._base_font: QFont | None = None
        install_zoom_shortcuts(self, on_zoom)

        self.setWindowFlags(transparent_window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # The container itself should not grab focus on click — only the
        # input line. Otherwise clicking the strip steals focus from the
        # user's current app.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._input = QLineEdit(self)
        self._input.setPlaceholderText("Talk to your buddy (or /help)")
        self._input.setFixedHeight(_DOCK_INPUT_HEIGHT)
        self._input.setStyleSheet(glass_pill_stylesheet())
        self._input.returnPressed.connect(self._submit)
        # The line edit is the only focusable child. The transparent
        # parent is NoFocus so tabbing or clicking the strip never pulls
        # activation.
        self._input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        apply_drop_shadow(
            self._input,
            blur=4,
            offset=(0, 0),
            color=QColor(0, 0, 0, 255),
        )
        layout.addWidget(self._input, 0)

        self._status = QLabel("ready", self)
        self._status.setStyleSheet(
            "color: #ffffff; background: transparent; padding: 0 4px;"
        )
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self._status.setMinimumWidth(0)
        apply_drop_shadow(
            self._status,
            blur=4,
            offset=(0, 0),
            color=QColor(0, 0, 0, 255),
        )
        layout.addWidget(self._status, 0)

        self._input.setMinimumWidth(0)
        self.resize(*self._floating_size())

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        if self._on_submit is not None:
            self._on_submit(text)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def focus_input(self) -> None:
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def restore_floating_size(self) -> None:
        """Reset to the floating-pill default. Call after reparenting
        back out of an embedded layout that stretched the widget."""
        self.resize(*self._floating_size())

    def apply_font(self, font: QFont) -> None:
        self._base_font = QFont(font)
        self._reapply_font()

    def set_zoom(self, factor: float) -> None:
        """Rescale the dock by ``factor`` (1.0 = original size).
        Multiplies font size, input row height, status row height,
        and overall pill width."""
        if factor <= 0.0 or factor == self._zoom:
            return
        self._zoom = factor
        self._reapply_font()
        self._input.setFixedHeight(self._scaled(_DOCK_INPUT_HEIGHT))
        self.resize(*self._floating_size())

    def _floating_size(self) -> tuple[int, int]:
        return (
            self._scaled(_DOCK_DEFAULT_WIDTH),
            self._scaled(_DOCK_INPUT_HEIGHT + _DOCK_STATUS_ROW_HEIGHT),
        )

    def _scaled(self, value: int) -> int:
        return max(1, int(round(value * self._zoom)))

    def _reapply_font(self) -> None:
        if self._base_font is None:
            return
        self._input.setFont(scale_font(self._base_font, self._zoom))


class ChatHistoryWindow(TranslucentLogWindow):
    """Frameless translucent scrollable chat history.
    Starts hidden — ``toggle_chat_log`` shows it.
    """

    LOG_MAX_LINES = 500

    def __init__(
        self,
        *,
        buddy_name: str = "TokenPal",
        on_hide: Callable[[], None] | None = None,
        on_zoom: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(
            title=f"{buddy_name} — chat",
            default_size=_HISTORY_DEFAULT_SIZE,
            on_hide=on_hide,
            on_zoom=on_zoom,
        )

        # ChatDock reparents into this slot when the buddy is hidden.
        # Empty in the normal (buddy-visible) state, so the layout
        # collapses to zero height.
        self._dock_slot = QVBoxLayout()
        self._dock_slot.setContentsMargins(0, 0, 0, 0)
        self._dock_slot.setSpacing(0)
        self._extras_layout.addLayout(self._dock_slot)

    def embed_dock(self, dock: QWidget) -> None:
        """Mount the chat dock inside this window's bottom slot."""
        self._dock_slot.addWidget(dock)

    def release_dock(self, dock: QWidget) -> None:
        """Remove the dock from the slot so it can float again."""
        self._dock_slot.removeWidget(dock)

    def set_display_name(self, name: str) -> None:
        self.setWindowTitle(f"{name} — chat")
        self._drag_handle.set_title(name)

    def append_line(
        self,
        ts: float | None,
        author: str,
        text: str,
        url: str | None = None,
    ) -> None:
        ts_str = format_chat_ts(ts) if ts is not None else ""
        safe_author = escape(author)
        safe_text = escape(text)
        if url is not None:
            safe_text = (
                f'{safe_text} <a href="{escape(url, quote=True)}">'
                f"[link]</a>"
            )
        line = (
            f'<div style="margin: 2px 0">'
            f'<span style="color:#bbbbbb">{escape(ts_str)}</span> '
            f'<b>{safe_author}:</b> {safe_text}'
            f"</div>"
        )
        self._log.append(line)
        self._trim_to_cap()

    def load_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        for ts, author, text, url in entries:
            self.append_line(ts, author, text, url)

    def clear_log(self) -> None:
        self._log.clear()

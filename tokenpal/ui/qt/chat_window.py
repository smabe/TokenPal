"""Chat surfaces for the Qt frontend: ``ChatDock`` (always-visible
input + status strip anchored under the buddy) and
``ChatHistoryWindow`` (standalone scrollable log, starts hidden).
Shared styling lives in ``qt/_text_fx.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tokenpal.ui.chat_format import format_chat_ts
from tokenpal.ui.qt._text_fx import (
    apply_drop_shadow,
    glass_button_stylesheet,
    glass_pill_stylesheet,
    glass_scrollbar_stylesheet,
    transparent_window_flags,
)

_CHAT_LOG_MAX_LINES = 500
_DOCK_DEFAULT_WIDTH = 360
_DOCK_INPUT_HEIGHT = 32
_HISTORY_DEFAULT_SIZE = (520, 380)
_DRAG_HANDLE_HEIGHT = 22


class _DragHandle(QLabel):
    """Thin labeled strip at the top of the history window that users
    grab to reposition the frameless window.

    Walks up to the top-level window on press and moves it in the
    parent's coordinate space as the mouse moves. Kept local to this
    module because no one else needs a drag handle.
    """

    def __init__(self, title: str, *, parent: QWidget) -> None:
        super().__init__(parent)
        self.setText(f"≡  {title}")
        self.setFixedHeight(_DRAG_HANDLE_HEIGHT)
        self.setStyleSheet(glass_button_stylesheet(radius=8))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self._drag_offset: QPoint | None = None
        apply_drop_shadow(self, blur=6, offset=(0, 1))

    def set_title(self, title: str) -> None:
        self.setText(f"≡  {title}")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        win = self.window()
        if win is None:
            return
        self._drag_offset = (
            event.globalPosition().toPoint() - win.frameGeometry().topLeft()
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        win = self.window()
        if win is None:
            return
        new_pos = event.globalPosition().toPoint() - self._drag_offset
        win.move(new_pos)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = None
        event.accept()


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
    ) -> None:
        super().__init__()
        self._on_submit = on_submit

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
        apply_drop_shadow(self._input, blur=10, offset=(0, 2))
        layout.addWidget(self._input, 0)

        self._status = QLabel("ready", self)
        self._status.setStyleSheet(
            "color: #ffffff; background: transparent; padding: 0 4px;"
        )
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        # Ignored horizontal policy lets the label shrink below its
        # full text width when the containing window is resized
        # smaller than the status string's natural width. Text clips
        # at the label's right edge rather than locking the layout.
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self._status.setMinimumWidth(0)
        apply_drop_shadow(self._status, blur=8, offset=(0, 1))
        layout.addWidget(self._status, 0)

        # QLineEdit's default minimum width is substantial — shrink it
        # so the embedded dock tracks the history window's resize.
        self._input.setMinimumWidth(0)

        self.resize(_DOCK_DEFAULT_WIDTH, _DOCK_INPUT_HEIGHT + 26)

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
        self.resize(_DOCK_DEFAULT_WIDTH, _DOCK_INPUT_HEIGHT + 26)


class ChatHistoryWindow(QWidget):
    """Frameless translucent scrollable chat history with Hide button.
    Starts hidden — ``toggle_chat_log`` shows it.
    """

    def __init__(
        self,
        *,
        buddy_name: str = "TokenPal",
        on_hide: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{buddy_name} — chat")
        self.resize(*_HISTORY_DEFAULT_SIZE)
        self._on_hide = on_hide

        self.setWindowFlags(transparent_window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # QTextBrowser consumes its own mouse events for text selection,
        # so we need a dedicated drag strip for repositioning.
        self._drag_handle = _DragHandle(buddy_name, parent=self)
        layout.addWidget(self._drag_handle, 0)

        self._log = QTextBrowser(self)
        self._log.setOpenExternalLinks(True)
        self._log.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._log.viewport().setAutoFillBackground(False)
        self._log.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._log.setStyleSheet(
            "QTextBrowser { background: rgba(0, 0, 0, 0.28); "
            "color: #ffffff; border-radius: 10px; padding: 8px; }"
            + glass_scrollbar_stylesheet()
        )
        apply_drop_shadow(
            self._log, blur=12, offset=(0, 2), color=QColor(0, 0, 0, 200),
        )
        layout.addWidget(self._log, 1)

        # Placeholder slot where the floating ChatDock reparents itself
        # when the buddy is hidden. Empty in the normal (buddy-visible)
        # state, so the QVBoxLayout just collapses to zero height.
        self._dock_slot = QVBoxLayout()
        self._dock_slot.setContentsMargins(0, 0, 0, 0)
        self._dock_slot.setSpacing(0)
        layout.addLayout(self._dock_slot)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._hide_button = QPushButton("Hide", self)
        self._hide_button.setFixedHeight(26)
        self._hide_button.setStyleSheet(glass_button_stylesheet())
        apply_drop_shadow(self._hide_button, blur=8, offset=(0, 1))
        self._hide_button.clicked.connect(self._handle_hide_clicked)
        row.addWidget(self._hide_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        layout.addLayout(row)

    def embed_dock(self, dock: QWidget) -> None:
        """Mount the chat dock inside this window's bottom slot.

        The dock spans the full inner width so its left edge lines up
        with the log view's left edge exactly.
        """
        self._dock_slot.addWidget(dock)

    def release_dock(self, dock: QWidget) -> None:
        """Remove the dock from the slot so it can float again."""
        self._dock_slot.removeWidget(dock)

    def set_display_name(self, name: str) -> None:
        self.setWindowTitle(f"{name} — chat")
        self._drag_handle.set_title(name)

    def _handle_hide_clicked(self) -> None:
        if self._on_hide is not None:
            self._on_hide()
        else:
            self.hide()

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
        if self._log.document().blockCount() > _CHAT_LOG_MAX_LINES:
            self._trim_to_cap()

    def _trim_to_cap(self) -> None:
        doc = self._log.document()
        while doc.blockCount() > _CHAT_LOG_MAX_LINES:
            cursor = self._log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # leading newline

    def load_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        for ts, author, text, url in entries:
            self.append_line(ts, author, text, url)

    def clear_log(self) -> None:
        self._log.clear()

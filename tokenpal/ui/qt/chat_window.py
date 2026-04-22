"""Chat log + input window for the Qt frontend.

QMainWindow with:
- central scrollable chat log (QTextEdit, read-only)
- bottom input line (QLineEdit). Enter submits. Leading `/` routes to
  the command callback; anything else goes to the freeform input callback.
- status bar showing mood / model / voice / app / spoke-ago etc.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLineEdit,
    QMainWindow,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tokenpal.ui.chat_format import format_chat_ts

_CHAT_LOG_MAX_LINES = 500


class ChatWindow(QMainWindow):
    def __init__(
        self,
        *,
        on_submit: Callable[[str], None] | None = None,
        buddy_name: str = "TokenPal",
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{buddy_name} — chat")
        self.resize(520, 420)

        self._on_submit = on_submit
        self._line_count = 0

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._log = QTextBrowser(central)
        self._log.setOpenExternalLinks(True)
        layout.addWidget(self._log, 1)

        self._input = QLineEdit(central)
        self._input.setPlaceholderText("Talk to your buddy (or /help)")
        self._input.returnPressed.connect(self._submit)
        layout.addWidget(self._input, 0)

        self.setCentralWidget(central)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("ready")

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        if self._on_submit is not None:
            self._on_submit(text)

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
            f'<span style="color:#888">{escape(ts_str)}</span> '
            f'<b>{safe_author}:</b> {safe_text}'
            f"</div>"
        )
        self._log.append(line)
        self._line_count += 1
        if self._line_count > _CHAT_LOG_MAX_LINES:
            self._trim_to_cap()

    def _trim_to_cap(self) -> None:
        doc = self._log.document()
        while doc.blockCount() > _CHAT_LOG_MAX_LINES:
            cursor = self._log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # leading newline
        self._line_count = doc.blockCount()

    def load_history(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        for ts, author, text, url in entries:
            self.append_line(ts, author, text, url)

    def clear_log(self) -> None:
        self._log.clear()
        self._line_count = 0

    def set_status(self, text: str) -> None:
        self._status.showMessage(text)

    def focus_input(self) -> None:
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

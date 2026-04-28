"""Falsifiable smoothness gate for the qt-it audit.

Translates a 1-pixel-wide vertical white line horizontally across a
black QWidget at exactly 144 px/sec. Capture with iPhone 14+ slo-mo
(1000 fps) for 5 seconds; the line's position vs capture-frame should
plot as a straight line of slope (144/1000) px/frame.

- Plateaus  → dropped present (the screen showed the same frame twice).
- Doubled steps → buffered presents (DWM held a frame and dumped two).
- Curve / wobble → timer slip (pump cadence drifting against vsync).

Run with: python -m tests.manual.ruler_scroll

Env vars to toggle the Phase A levers individually so we can measure
which one moved the needle:

    TOKENPAL_RULER_TRANSLUCENT=1   WA_TranslucentBackground (matches buddy)
    TOKENPAL_RULER_REPAINT=1      synchronous repaint() instead of update()
    TOKENPAL_RULER_DWMFLUSH=1     DwmFlush() per pump (Windows only)
    TOKENPAL_RULER_VSYNC=1        pump rate from QScreen.refreshRate()
    TOKENPAL_RULER_SPEED=144      px/sec (default 144 to match 144 Hz panel)
    TOKENPAL_RULER_SECONDS=5      capture window length

Closes on any keypress or after TOKENPAL_RULER_SECONDS+2 seconds (so the
slo-mo capture has time to bracket the run).
"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QApplication, QWidget


def _env_flag(name: str) -> bool:
    return bool(os.environ.get(name))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        print(
            f"ruler_scroll: bad value for {name}={raw!r}; using {default}",
            file=sys.stderr, flush=True,
        )
        return default


_TRANSLUCENT = _env_flag("TOKENPAL_RULER_TRANSLUCENT")
_REPAINT = _env_flag("TOKENPAL_RULER_REPAINT")
_DWMFLUSH = _env_flag("TOKENPAL_RULER_DWMFLUSH")
_VSYNC_PUMP = _env_flag("TOKENPAL_RULER_VSYNC")
_SPEED_PX_S = _env_float("TOKENPAL_RULER_SPEED", 144.0)
_SECONDS = _env_float("TOKENPAL_RULER_SECONDS", 5.0)

_BLACK = QColor(Qt.GlobalColor.black)
_WHITE = QColor(Qt.GlobalColor.white)

if sys.platform == "win32":
    import ctypes

    _DwmFlush = ctypes.windll.dwmapi.DwmFlush  # type: ignore[attr-defined]
else:
    _DwmFlush = None


class RulerWindow(QWidget):
    """1px white line scrolling at constant px/sec across a black field."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ruler scroll — qt-it smoothness gate")
        if _TRANSLUCENT:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QGuiApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else None
        if geom is not None:
            self.resize(geom.width(), 240)
            self.move(geom.left(), geom.top() + (geom.height() - 240) // 2)
        else:
            self.resize(1920, 240)

        refresh_hz = float(screen.refreshRate()) if screen else 60.0
        if _VSYNC_PUMP and refresh_hz > 0:
            interval_ms = max(int(round(1000.0 / refresh_hz)), 1)
        else:
            interval_ms = 6

        self._t0 = time.monotonic()
        self._x_pixel = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        QTimer.singleShot(int((_SECONDS + 2.0) * 1000), self.close)

        print(
            f"ruler_scroll: speed={_SPEED_PX_S:.0f}px/s seconds={_SECONDS:.0f} "
            f"pump={interval_ms}ms ({1000.0 / interval_ms:.1f}Hz) "
            f"refresh={refresh_hz:.1f}Hz translucent={_TRANSLUCENT} "
            f"repaint={_REPAINT} dwmflush={_DWMFLUSH} vsync_pump={_VSYNC_PUMP}",
            flush=True,
        )

    def _on_tick(self) -> None:
        elapsed = time.monotonic() - self._t0
        x = (elapsed * _SPEED_PX_S) % max(self.width(), 1)
        self._x_pixel = int(x)
        if _REPAINT:
            self.repaint()
        else:
            self.update()
        if _DWMFLUSH and _DwmFlush is not None:
            _DwmFlush()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        if not _TRANSLUCENT:
            painter.fillRect(self.rect(), _BLACK)
        painter.fillRect(self._x_pixel, 0, 1, self.height(), _WHITE)

    def keyPressEvent(self, _event: QKeyEvent) -> None:
        self.close()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = RulerWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Frameless, transparent, always-on-top buddy window.

Phase 2 scope: render a static ASCII frame, accept mouse drag (moves the
anchor, physics trails the body), and tick a 60 Hz physics loop. No
brain wiring — that lands in Phase 3.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.palette import BUDDY_GREEN
from tokenpal.ui.qt.markup import parse_markup, stripped_text
from tokenpal.ui.qt.physics import DangleSimulator, PhysicsConfig, run_until_settled

_PHYSICS_HZ = 60
_TICK_MS = int(1000 / _PHYSICS_HZ)
_FLING_SAMPLE_WINDOW_S = 0.08  # how much recent cursor motion counts as fling
_FLING_SAMPLE_MAX = 32          # ring-buffer cap
_EDGE_DOCK_THRESHOLD = 20       # px from screen edge triggers snap

_FG_COLOR = QColor(BUDDY_GREEN)


def _measure_block_paint_width(font: QFont) -> int:
    """Return the width in pixels that a single U+2588 FULL BLOCK glyph
    actually paints with ``font``. Used as the fixed grid step for the
    buddy art so neighbouring blocks visually touch — see the note in
    ``BuddyWindow._resize_to_frame`` for why we don't trust the font's
    reported advance."""
    img = QImage(64, 32, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    painter.setFont(font)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("white"))
    painter.drawText(8, 24, "█")
    painter.end()
    lo, hi = None, None
    for x in range(img.width()):
        for y in range(img.height()):
            if img.pixelColor(x, y).alpha() > 20:
                lo = x if lo is None else lo
                hi = x
                break
    if lo is None or hi is None:
        return max(1, int(font.pointSize() * 0.7))
    return max(1, hi - lo + 1)


class BuddyWindow(QWidget):
    """The draggable, dangleable buddy.

    Drag moves the **anchor**. The spring pulls the body. On release,
    any residual cursor velocity is injected as an impulse so a fast
    whip sends the buddy swinging.
    """

    # Fires whenever the body physically moves — either because the
    # physics tick advanced the simulator or because ``set_frame``
    # resized the window. Consumers (the speech bubble) use this to
    # follow the buddy across the screen.
    position_changed = Signal()


    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        physics_config: PhysicsConfig | None = None,
        font_family: str = "Courier",
        font_size: int = 14,
    ) -> None:
        super().__init__()
        self._frame_lines = list(frame_lines)
        self._on_right_click: Callable[[QPoint], None] | None = None

        self._font = QFont(font_family, font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Qt.Tool on macOS maps to NSWindow utility-panel behavior that
        # hides the window whenever the app loses focus — so clicking
        # the desktop would make the buddy vanish. On Windows / Linux
        # it's the right flag for "don't show in taskbar"; on macOS we
        # rely on LSUIElement-equivalent (apply_macos_accessory_mode)
        # plus the NSWindow collectionBehavior tweak applied after the
        # window is native (see qt/platform.apply_macos_stay_visible).
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._sim = DangleSimulator(
            anchor=initial_anchor,
            initial_pos=initial_anchor,
            config=physics_config,
        )
        # Pre-settle at construction so the buddy doesn't visibly fall
        # from the initial position on first show. `run_until_settled`
        # enforces a budget so a pathological config can't hang __init__.
        run_until_settled(self._sim)

        self._resize_to_frame()
        self._move_to_body_position()

        self._drag_active = False
        self._grab_offset = QPoint(0, 0)
        self._fling_samples: deque[tuple[float, QPointF]] = deque(
            maxlen=_FLING_SAMPLE_MAX,
        )

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._last_tick_ts = time.monotonic()
        # Don't start the timer — buddy starts settled. It kicks off on
        # the first drag/impulse. See _wake_timer / _sleep_timer.

    def set_right_click_handler(
        self, handler: Callable[[QPoint], None] | None,
    ) -> None:
        self._on_right_click = handler

    def set_frame(self, lines: list[str]) -> None:
        self._frame_lines = list(lines)
        self._resize_to_frame()
        self.update()

    def _resize_to_frame(self) -> None:
        fm = self.fontMetrics()
        # Treat the art as a fixed-pitch grid rather than trusting
        # ``horizontalAdvance`` on the whole line. Menlo's reported
        # advance for block glyphs (U+2588 and friends) includes side
        # bearings that the painter doesn't actually draw — the glyph
        # pixels are ~10 px wide but the reported advance is 13. Step
        # by the glyph's *painted* width instead so neighbouring blocks
        # visually touch, matching the tight grid the Textual overlay
        # produces.
        # Step by one pixel less than the painted glyph width. Block
        # glyphs have a faint anti-aliased edge column; stepping exactly
        # at the glyph width leaves a 1-pixel low-alpha seam between
        # neighbours that reads as vertical striping in large ``▓``
        # fills. A 1 px overlap merges them into a solid field.
        self._cell_w = max(_measure_block_paint_width(self._font) - 1, 1)
        cols = max((len(stripped_text(line)) for line in self._frame_lines), default=0)
        self._cols = cols
        # Use ascent as the row step rather than the full line height.
        # ``fm.height()`` includes the descent gap the font reserves for
        # glyphs like `g`/`y`, but full-block art never descends below
        # the baseline — the extra padding just opens vertical gaps
        # between stacked blocks. Ascent keeps same-glyph rows touching
        # and minimises gaps where half-blocks (▀ ▄) meet.
        self._line_h = fm.ascent()
        self.resize(
            cols * self._cell_w + 12,
            self._line_h * len(self._frame_lines) + fm.descent() + 8,
        )

    def _move_to_body_position(self) -> None:
        x, y = self._sim.position
        # Physics tracks the anchor (top-center attach point of the
        # buddy). The window's origin is the top-left, so shift by half
        # the width to center the buddy under the anchor.
        offset_x = self.width() // 2
        self.move(int(x) - offset_x, int(y))
        self.position_changed.emit()

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick_ts
        self._last_tick_ts = now
        self._sim.tick(min(dt, 1.0 / 30.0))  # clamp dt to survive stalls
        self._move_to_body_position()
        if self._sim.sleeping and not self._drag_active:
            self._sleep_timer()

    def _wake_timer(self) -> None:
        if not self._timer.isActive():
            self._last_tick_ts = time.monotonic()
            self._timer.start()

    def _sleep_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    # --- Input -----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if self._on_right_click is not None:
                self._on_right_click(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_active = True
        anchor_x, anchor_y = self._sim.anchor
        cursor = event.globalPosition()
        self._grab_offset = QPoint(
            int(cursor.x() - anchor_x), int(cursor.y() - anchor_y),
        )
        self._fling_samples.clear()
        self._fling_samples.append((time.monotonic(), cursor))
        self._wake_timer()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_active:
            return
        cursor = event.globalPosition()
        now = time.monotonic()
        new_anchor = (
            cursor.x() - self._grab_offset.x(),
            cursor.y() - self._grab_offset.y(),
        )
        self._sim.set_anchor(*new_anchor)
        self._fling_samples.append((now, cursor))
        # Pop-left any samples older than the fling window. O(k) where
        # k is the number of expired entries — bounded by the maxlen cap
        # and the 80 ms window, so small and amortized O(1) per move.
        cutoff = now - _FLING_SAMPLE_WINDOW_S
        while self._fling_samples and self._fling_samples[0][0] < cutoff:
            self._fling_samples.popleft()
        self._wake_timer()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._drag_active:
            return
        self._drag_active = False
        if len(self._fling_samples) >= 2:
            t0, p0 = self._fling_samples[0]
            t1, p1 = self._fling_samples[-1]
            span = max(t1 - t0, 1e-3)
            vx = (p1.x() - p0.x()) / span
            vy = (p1.y() - p0.y()) / span
            self._sim.apply_impulse(vx, vy)
        self._fling_samples.clear()
        self._maybe_edge_dock()
        # Keep the timer running — the body may still be swinging.

    def _maybe_edge_dock(self) -> None:
        """Snap the anchor to the nearest screen edge when dropped close
        to one. Keeps the buddy feeling "sticky" to monitor boundaries
        and covers the multi-monitor case via QScreen lookup at the
        anchor's current position."""
        anchor_x, anchor_y = self._sim.anchor
        screen = QGuiApplication.screenAt(QPoint(int(anchor_x), int(anchor_y)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        new_x, new_y = anchor_x, anchor_y
        if anchor_x - geom.left() < _EDGE_DOCK_THRESHOLD:
            new_x = geom.left()
        elif geom.right() - anchor_x < _EDGE_DOCK_THRESHOLD:
            new_x = geom.right()
        if anchor_y - geom.top() < _EDGE_DOCK_THRESHOLD:
            new_y = geom.top()
        elif geom.bottom() - anchor_y < _EDGE_DOCK_THRESHOLD:
            new_y = geom.bottom()
        if (new_x, new_y) != (anchor_x, anchor_y):
            self._sim.set_anchor(new_x, new_y)

    # --- Render ----------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        # WA_TranslucentBackground already clears the region to fully
        # transparent before this runs, so no fillRect needed.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)
        fm = self.fontMetrics()
        line_h = self._line_h
        cell_w = self._cell_w
        y = fm.ascent() + 4
        # Lay out each character on a fixed-pitch grid. See _resize_to_frame
        # for the font-metrics quirk that makes this necessary.
        total_w = self._cols * cell_w
        for line in self._frame_lines:
            chars = len(stripped_text(line))
            base_x = 6 + (total_w - chars * cell_w) // 2
            col = 0
            for seg in parse_markup(line):
                painter.setPen(QColor(seg.color) if seg.color else _FG_COLOR)
                for ch in seg.text:
                    painter.drawText(base_x + col * cell_w, y, ch)
                    col += 1
            y += line_h

"""QQuickWindow host for the buddy.

Wraps a hidden ``BuddyWindow`` (QWidget) as the physics + geometry
model. The window is sized to span the primary screen and never moved
-- moving the window via SetWindowPos every frame stalls the Windows
compositor (visible as 7-15 ms vsync gaps + microsecond catch-up
bursts). The buddy moves *inside* the window via QQuickItem position,
which is a pure scene-graph property change with no Win32 round-trip.
This is the standard game-engine pattern: fixed window, content moves
inside.

Click-through via ``ClickThroughToggle`` (Windows only). The
QQuickItem hierarchy is::

    contentItem
    └─ pivot (positioned at COM in window coords; rotated by theta)
       └─ buddy_item (offset -com_art so art-COM lands at pivot origin)

This composition is necessary because ``QQuickItem.TransformOrigin``
exposes only nine discrete pivot points. The COM is head-heavy
(``_COM_Y_FRACTION = 0.30``) so it does not coincide with any of them.
"""
from __future__ import annotations

import math
import os
import sys
import time

from PySide6.QtCore import QPointF, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickWindow

from tokenpal.ui.qt.buddy_window import (
    BUBBLE_HOVER_OFFSET_Y,
    DOCK_OFFSET_Y,
    BuddyWindow,
)
from tokenpal.ui.quick._clickthrough import ClickThroughToggle
from tokenpal.ui.quick.bubble_item import BubbleQuickItem
from tokenpal.ui.quick.buddy_item import BuddyQuickItem
from tokenpal.ui.quick.dock_mock_item import DockMockQuickItem
from tokenpal.ui.quick.grip_item import GripQuickItem

_TRACE = bool(os.environ.get("TOKENPAL_QUICK_TRACE"))
_TRACE_EVERY = int(os.environ.get("TOKENPAL_QUICK_TRACE_EVERY", "1"))
_TRACE_PATH = os.environ.get(
    "TOKENPAL_QUICK_TRACE_PATH",
    os.path.expanduser("~/tokenpal-quick-trace.log"),
)


class BuddyQuickWindow(QQuickWindow):
    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        font_family: str = "Courier",
        font_size: int = 14,
    ) -> None:
        super().__init__()
        self.setColor(QColor(Qt.GlobalColor.transparent))
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setFlags(flags)

        # Fixed window covering the primary screen -- never moved.
        # Spanning the full virtualGeometry across all attached
        # screens *almost* works: the buddy can slide across the
        # screen edge -- but if the secondary monitor has a different
        # DPR than the primary, Qt's per-monitor DPI handling against
        # a single DirectComposition surface produces a disjointed /
        # double-composite render on the secondary screen. The right
        # fix is one QQuickWindow per screen with reparenting on edge
        # cross (parking-lot item in plans/qt-it-quick-migration.md).
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            geo = primary.geometry()
            self.setPosition(geo.x(), geo.y())
            self.resize(geo.width(), geo.height())
            self._virtual_origin = (geo.x(), geo.y())
        else:
            self._virtual_origin = (0, 0)

        self._model = BuddyWindow(
            frame_lines=frame_lines,
            initial_anchor=initial_anchor,
            font_family=font_family,
            font_size=font_size,
        )
        self._model.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        # Suppress the QWidget paint path -- nothing visible to render
        # there, and we don't want to spend cycles on it.
        self._model.paintEvent = lambda _event: None  # type: ignore[method-assign]
        # Offscreen-rescue tick bails on !isVisible(); WA_DontShowOnScreen
        # keeps this off the native window list.
        self._model.show()
        # Phase-lock physics to vsync. A QTimer at 4 ms beats against
        # FIXED_DT=4.166 ms; about every 30 frames no physics step
        # drains between two vsyncs and the SAME theta is painted twice,
        # producing a visible "skip a beat". frameSwapped fires once
        # per present, so we get exactly one physics tick + lerp + paint
        # per vsync, alpha pinned at 1.0.
        self._model._timer.stop()
        self._model._wake_timer = lambda: None  # type: ignore[method-assign]
        self._model._sleep_timer = lambda: None  # type: ignore[method-assign]
        self.frameSwapped.connect(self._on_sync_tick)
        # Fallback: when nothing is painting (buddy settled, no
        # update() calls), frameSwapped stops firing. Kick a paint at
        # ~60 Hz so physics wakes on demand. Uses Qt.CoarseTimer because
        # this is just a heartbeat, not a frame driver.
        self._kick_timer = QTimer(self)
        self._kick_timer.setInterval(16)
        self._kick_timer.timeout.connect(lambda: self._buddy_item.update())

        self._pivot = QQuickItem()
        self._pivot.setParentItem(self.contentItem())
        self._pivot.setSize(QSizeF(0.0, 0.0))
        self._pivot.setTransformOrigin(QQuickItem.TransformOrigin.TopLeft)

        self._buddy_item = BuddyQuickItem(self._model)
        self._buddy_item.setParentItem(self._pivot)

        self._bubble_item = BubbleQuickItem(
            font_family=font_family, font_size=font_size,
        )
        self._bubble_item.setParentItem(self._pivot)

        self._dock_mock_item = DockMockQuickItem()
        self._dock_mock_item.setParentItem(self._pivot)

        self._grip_item = GripQuickItem()
        self._grip_item.setParentItem(self._pivot)

        # Cache the art-geometry tuple driving follower anchor placement
        # + buddy-item bounds. Recomputing those is fine; pushing them
        # to the scene graph 240x/sec when nothing changed dirties
        # transform nodes and emits geometry-change signals.
        self._last_art_geom: tuple[int, int, float, float] | None = None

        self._click_through = ClickThroughToggle(
            self, self._opaque_probe, parent=self,
        )
        if not os.environ.get("TOKENPAL_QUICK_NO_CLICKTHROUGH"):
            self._click_through.start()

        self._trace_count = 0
        self._trace_t0 = time.perf_counter()
        self._trace_fp = None
        if _TRACE:
            self._trace_fp = open(_TRACE_PATH, "w", buffering=1, encoding="utf-8")
            print(f"[trace] writing to {_TRACE_PATH}")
            self.frameSwapped.connect(self._on_frame_swapped_trace)

        self._sync_geometry()
        self._kick_timer.start()
        # Bootstrap the frameSwapped chain: schedule an initial paint so
        # the first vsync fires our sync.
        self._buddy_item.update()

    def _on_frame_swapped_trace(self) -> None:
        t = (time.perf_counter() - self._trace_t0) * 1000.0
        if self._trace_fp is not None:
            self._trace_fp.write(f"FS  t={t:8.3f}ms\n")

    @property
    def model(self) -> BuddyWindow:
        return self._model

    @property
    def buddy_item(self) -> BuddyQuickItem:
        return self._buddy_item

    @property
    def bubble_item(self) -> BubbleQuickItem:
        return self._bubble_item

    @property
    def dock_mock_item(self) -> DockMockQuickItem:
        return self._dock_mock_item

    @property
    def grip_item(self) -> GripQuickItem:
        return self._grip_item

    def _opaque_probe(self, client_point: QPointF) -> bool:
        if self._buddy_item.contains(
            self._buddy_item.mapFromScene(client_point),
        ):
            return True
        if self._bubble_item.isVisible() and self._bubble_item.contains(
            self._bubble_item.mapFromScene(client_point),
        ):
            return True
        if self._grip_item.isVisible() and self._grip_item.contains(
            self._grip_item.mapFromScene(client_point),
        ):
            return True
        return False

    def _clamped_lerp(self) -> tuple[float, float, float]:
        """Like ``BuddyWindow._lerped_state`` but clamps theta alpha to
        [0, 1]. The QWidget path repaints synchronously inside _on_tick
        so alpha is always near 1; the Quick path's vsync paint can land
        mid-pump-interval at alpha up to ~2, where the model's theta
        extrapolation oscillates against the next pump's actual physics
        state and produces visible back-stepping. Position alpha is
        already clamped in the model -- we just override theta."""
        from tokenpal.ui.qt.buddy_window import _FIXED_DT_S
        m = self._model
        sample_ts = (
            m._paint_target_ts
            if m._paint_target_ts is not None
            else time.monotonic()
        )
        delta_s = max(0.0, sample_ts - m._last_step_ts)
        alpha = max(0.0, min(1.0, delta_s / _FIXED_DT_S))
        delta_theta = m._sim.theta - m._theta_prev
        if delta_theta > math.pi:
            delta_theta -= 2.0 * math.pi
        elif delta_theta < -math.pi:
            delta_theta += 2.0 * math.pi
        theta = m._theta_prev + delta_theta * alpha
        px, py = m._sim.position
        ppx, ppy = m._pos_prev
        return (theta, ppx + (px - ppx) * alpha, ppy + (py - ppy) * alpha)

    def _on_sync_tick(self) -> None:
        # Drain physics (advances _last_step_ts to now, sets
        # _paint_target_ts to next vsync inside _on_tick), then sync the
        # scene graph. Coherent: alpha stays near 0.96 every frame.
        self._model._on_tick()
        self._sync_geometry()
        if _TRACE:
            self._trace_count += 1
            if self._trace_count % _TRACE_EVERY == 0:
                self._dump_trace()

    def _dump_trace(self) -> None:
        m = self._model
        t = (time.perf_counter() - self._trace_t0) * 1000.0
        sx, sy = m._sim.position
        ppx, ppy = m._pos_prev
        last_ts_ms = (m._last_step_ts - self._trace_t0_mono()) * 1000.0
        paint_ts_ms = (
            (m._paint_target_ts - self._trace_t0_mono()) * 1000.0
            if m._paint_target_ts is not None else float("nan")
        )
        theta_l, cx_l, cy_l = self._clamped_lerp()
        from tokenpal.ui.qt.buddy_window import _FIXED_DT_S
        sample_ts = m._paint_target_ts or time.monotonic()
        alpha_raw = (sample_ts - m._last_step_ts) / _FIXED_DT_S
        line = (
            f"ST  t={t:8.3f}ms "
            f"sim θ={m._sim.theta:+.4f} prev={m._theta_prev:+.4f} "
            f"sim pos=({sx:7.2f},{sy:7.2f}) prev=({ppx:7.2f},{ppy:7.2f}) "
            f"last_step={last_ts_ms:8.3f} paint={paint_ts_ms:8.3f} "
            f"α={alpha_raw:+.3f} "
            f"lerp θ={theta_l:+.4f} pos=({cx_l:7.2f},{cy_l:7.2f})"
        )
        if self._trace_fp is not None:
            self._trace_fp.write(line + "\n")

    def _trace_t0_mono(self) -> float:
        # Anchor monotonic timestamps to the perf_counter t0 for readable
        # millisecond offsets. Cache after first call.
        if not hasattr(self, "_t0_mono"):
            self._t0_mono = time.monotonic() - (
                time.perf_counter() - self._trace_t0
            )
        return self._t0_mono

    def _sync_geometry(self) -> None:
        m = self._model
        theta, cx_lerp, cy_lerp = self._clamped_lerp()

        # Pivot lives in window-local coords. The window spans the
        # virtual desktop, so window-local = screen - virtualGeometry
        # topLeft (which can be negative when secondary screens sit
        # left of / above the primary). The simulator's lerp output
        # is in screen coords, so subtract the offset.
        vox, voy = self._virtual_origin
        self._pivot.setX(cx_lerp - float(vox))
        self._pivot.setY(cy_lerp - float(voy))
        self._pivot.setRotation(math.degrees(theta))
        self._buddy_item.update()

        # Art geometry only changes on zoom / voice-frame swap; gate
        # follower anchors + buddy-item bounds so we're not pushing
        # the same numbers to the scene graph 240x/sec.
        com_x_art, com_y_art = m._com_art()
        geom = (m._art_w, m._art_h, com_x_art, com_y_art)
        if geom == self._last_art_geom:
            return
        self._last_art_geom = geom

        self._buddy_item.setX(-com_x_art)
        self._buddy_item.setY(-com_y_art)
        self._buddy_item.setWidth(m._art_w)
        self._buddy_item.setHeight(m._art_h)

        # Followers anchor in pivot-local space; pivot rotation gives
        # them the body-aligned offset for free.
        head_x = m._art_w / 2.0 - com_x_art
        self._bubble_item.set_anchor_in_parent(
            head_x, -com_y_art - float(BUBBLE_HOVER_OFFSET_Y),
        )
        self._dock_mock_item.set_anchor_in_parent(
            head_x, float(m._art_h) - com_y_art + float(DOCK_OFFSET_Y),
        )
        self._grip_item.set_anchor_in_parent(
            float(m._art_w) - com_x_art, float(m._art_h) - com_y_art,
        )

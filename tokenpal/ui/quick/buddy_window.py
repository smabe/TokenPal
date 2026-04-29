"""QQuick host for the buddy.

One ``QQuickWindow`` per attached screen, each spanning that screen's
geometry and never moved. The buddy items live under a single pivot
``QQuickItem`` that is reparented to whichever window currently
contains the buddy's COM. Only the active window drives physics via
``frameSwapped``; the others render empty transparent surfaces.

Why per-screen (not one virtual-desktop window): a single
``QQuickWindow`` spanning ``virtualGeometry`` produces a disjointed /
double-composite render on a secondary screen with a different DPR
than the primary, because Qt's per-monitor DPI scaling can't apply
cleanly to one DirectComposition surface that crosses a DPR boundary.

Why fixed windows (not moving SetWindowPos every frame): moving a
translucent window via SetWindowPos stalls the Windows compositor —
visible as 7-15 ms vsync gaps + microsecond catch-up bursts. The
buddy moves *inside* the active window via QQuickItem position, which
is a pure scene-graph property change with no Win32 round-trip.

Click-through via per-window ``ClickThroughToggle`` (Windows only).
The QQuickItem hierarchy on the active window is::

    contentItem
    └─ pivot (positioned at COM in window-local coords; rotated by theta)
       └─ buddy_item (offset -com_art so art-COM lands at pivot origin)
       └─ bubble_item / dock_mock_item / grip_item

Composition is necessary because ``QQuickItem.TransformOrigin`` exposes
only nine discrete pivot points; the COM is head-heavy
(``COM_Y_FRACTION = 0.30``) and does not coincide with any of them.
"""
from __future__ import annotations

import math
import os
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QPointF, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QScreen
from PySide6.QtQuick import QQuickItem, QQuickWindow

from tokenpal.ui.buddy_core import BuddyCore
from tokenpal.ui.qt.buddy_window import BUBBLE_HOVER_OFFSET_Y, DOCK_OFFSET_Y
from tokenpal.ui.qt.platform import buddy_overlay_flags
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


class _ScreenWindow(QQuickWindow):
    """One transparent QQuickWindow pinned to a single screen."""

    def __init__(self, screen: QScreen) -> None:
        super().__init__()
        self.screen_ref = screen
        self.setColor(QColor(Qt.GlobalColor.transparent))
        self.setFlags(buddy_overlay_flags())
        geo = screen.geometry()
        self.setPosition(geo.x(), geo.y())
        self.resize(geo.width(), geo.height())
        self.virtual_origin: tuple[int, int] = (geo.x(), geo.y())


class BuddyQuickWindow(QObject):
    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        font_family: str = "Courier",
        font_size: int = 14,
    ) -> None:
        super().__init__()

        self._core = BuddyCore(
            frame_lines=frame_lines,
            initial_anchor=initial_anchor,
            font_family=font_family,
            font_size=font_size,
            parent=self,
        )
        # Physics drives off frameSwapped (one tick per vsync, alpha
        # pinned near 1) — the core's own QTimer beats against FIXED_DT
        # and produces visible skips at 240 Hz. We never start it.

        self._pivot = QQuickItem()
        self._pivot.setSize(QSizeF(0.0, 0.0))
        self._pivot.setTransformOrigin(QQuickItem.TransformOrigin.TopLeft)

        self._buddy_item = BuddyQuickItem(self._core)
        self._buddy_item.setParentItem(self._pivot)

        self._bubble_item = BubbleQuickItem(
            font_family=font_family, font_size=font_size,
        )
        self._bubble_item.setParentItem(self._pivot)

        self._dock_mock_item = DockMockQuickItem()
        self._dock_mock_item.setParentItem(self._pivot)

        self._grip_item = GripQuickItem()
        self._grip_item.setParentItem(self._pivot)

        self._windows: list[_ScreenWindow] = []
        self._screen_to_window: dict[QScreen, _ScreenWindow] = {}
        self._click_through: dict[_ScreenWindow, ClickThroughToggle] = {}
        for screen in QGuiApplication.screens():
            w = _ScreenWindow(screen)
            self._windows.append(w)
            self._screen_to_window[screen] = w
            self._click_through[w] = ClickThroughToggle(
                w, self._make_probe(w), parent=w,
            )

        # Pushing the same art-geometry to the scene graph 240x/sec
        # dirties transform nodes and emits geometry-change signals.
        self._last_art_geom: tuple[int, int, float, float] | None = None

        ax, ay = self._core.sim.position
        self._active: _ScreenWindow = (
            self._pick_screen(ax, ay) or self._windows[0]
        )
        self._pivot.setParentItem(self._active.contentItem())
        self._active.frameSwapped.connect(self._on_sync_tick)

        # frameSwapped pauses when nothing is painting; a 60 Hz kick wakes
        # physics on demand.
        self._kick_timer = QTimer(self)
        self._kick_timer.setInterval(16)
        self._kick_timer.timeout.connect(lambda: self._buddy_item.update())

        self._trace_count = 0
        self._trace_t0 = time.perf_counter()
        self._trace_fp = None
        if _TRACE:
            self._trace_fp = open(_TRACE_PATH, "w", buffering=1, encoding="utf-8")
            print(f"[trace] writing to {_TRACE_PATH}")
            self._active.frameSwapped.connect(self._on_frame_swapped_trace)

        self._sync_geometry()
        self._kick_timer.start()
        self._buddy_item.update()

    # --- public API consumed by QtOverlay -----------------------------

    @property
    def core(self) -> BuddyCore:
        return self._core

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

    @property
    def active_window(self) -> _ScreenWindow:
        return self._active

    def show(self) -> None:
        for w in self._windows:
            w.show()
        if not os.environ.get("TOKENPAL_QUICK_NO_CLICKTHROUGH"):
            for ct in self._click_through.values():
                ct.start()

    def hide(self) -> None:
        for ct in self._click_through.values():
            ct.stop()
        for w in self._windows:
            w.hide()

    def raise_(self) -> None:
        for w in self._windows:
            w.raise_()

    def close(self) -> None:
        for ct in self._click_through.values():
            ct.stop()
        for w in self._windows:
            w.close()
        if self._trace_fp is not None:
            self._trace_fp.close()
            self._trace_fp = None

    # --- multi-screen plumbing ---------------------------------------

    def _pick_screen(self, x: float, y: float) -> _ScreenWindow | None:
        screen = QGuiApplication.screenAt(QPoint(int(x), int(y)))
        return self._screen_to_window.get(screen) if screen else None

    def _make_probe(
        self, w: _ScreenWindow,
    ) -> Callable[[QPointF], bool]:
        def probe(client_point: QPointF) -> bool:
            if self._active is not w:
                return False
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
        return probe

    def _switch_active(self, target: _ScreenWindow) -> None:
        if target is self._active:
            return
        self._active.frameSwapped.disconnect(self._on_sync_tick)
        if _TRACE:
            self._active.frameSwapped.disconnect(self._on_frame_swapped_trace)
        self._pivot.setParentItem(target.contentItem())
        self._active = target
        self._active.frameSwapped.connect(self._on_sync_tick)
        if _TRACE:
            self._active.frameSwapped.connect(self._on_frame_swapped_trace)
        # Textures are bound to the source window's scene graph.
        self._invalidate_textures()
        # Force re-push: pivot moved into a window with a different
        # virtual_origin.
        self._last_art_geom = None

    def _invalidate_textures(self) -> None:
        self._buddy_item._texture = None
        self._buddy_item._cached_pixmap_id = None
        for it in (self._bubble_item, self._dock_mock_item, self._grip_item):
            it._texture = None
            it._tex_image_id = None

    # --- physics + paint loop ----------------------------------------

    def _on_frame_swapped_trace(self) -> None:
        t = (time.perf_counter() - self._trace_t0) * 1000.0
        if self._trace_fp is not None:
            self._trace_fp.write(f"FS  t={t:8.3f}ms\n")

    def _on_sync_tick(self) -> None:
        self._core._on_tick()
        cx, cy = self._core.sim.position
        target = self._pick_screen(cx, cy)
        if target is not None and target is not self._active:
            self._switch_active(target)
        self._sync_geometry()
        if _TRACE:
            self._trace_count += 1
            if self._trace_count % _TRACE_EVERY == 0:
                self._dump_trace()

    def _dump_trace(self) -> None:
        from tokenpal.ui.buddy_core import FIXED_DT_S
        c = self._core
        t = (time.perf_counter() - self._trace_t0) * 1000.0
        sx, sy = c.sim.position
        ppx, ppy = c.pos_prev
        last_ts_ms = (c.last_step_ts - self._trace_t0_mono()) * 1000.0
        paint_ts_ms = (
            (c.paint_target_ts - self._trace_t0_mono()) * 1000.0
            if c.paint_target_ts is not None else float("nan")
        )
        theta_l, cx_l, cy_l = c.lerped_state_clamped()
        sample_ts = c.paint_target_ts or time.monotonic()
        alpha_raw = (sample_ts - c.last_step_ts) / FIXED_DT_S
        line = (
            f"ST  t={t:8.3f}ms "
            f"sim θ={c.sim.theta:+.4f} prev={c.theta_prev:+.4f} "
            f"sim pos=({sx:7.2f},{sy:7.2f}) prev=({ppx:7.2f},{ppy:7.2f}) "
            f"last_step={last_ts_ms:8.3f} paint={paint_ts_ms:8.3f} "
            f"α={alpha_raw:+.3f} "
            f"lerp θ={theta_l:+.4f} pos=({cx_l:7.2f},{cy_l:7.2f})"
        )
        if self._trace_fp is not None:
            self._trace_fp.write(line + "\n")

    def _trace_t0_mono(self) -> float:
        if not hasattr(self, "_t0_mono"):
            self._t0_mono = time.monotonic() - (
                time.perf_counter() - self._trace_t0
            )
        return self._t0_mono

    def _sync_geometry(self) -> None:
        c = self._core
        theta, cx_lerp, cy_lerp = c.lerped_state_clamped()

        vox, voy = self._active.virtual_origin
        self._pivot.setX(cx_lerp - float(vox))
        self._pivot.setY(cy_lerp - float(voy))
        self._pivot.setRotation(math.degrees(theta))
        self._buddy_item.update()

        com_x_art, com_y_art = c.com_art()
        geom = (c.art_w, c.art_h, com_x_art, com_y_art)
        if geom == self._last_art_geom:
            return
        self._last_art_geom = geom

        self._buddy_item.setX(-com_x_art)
        self._buddy_item.setY(-com_y_art)
        self._buddy_item.setWidth(c.art_w)
        self._buddy_item.setHeight(c.art_h)

        head_x = c.art_w / 2.0 - com_x_art
        self._bubble_item.set_anchor_in_parent(
            head_x, -com_y_art - float(BUBBLE_HOVER_OFFSET_Y),
        )
        self._dock_mock_item.set_anchor_in_parent(
            head_x, float(c.art_h) - com_y_art + float(DOCK_OFFSET_Y),
        )
        self._grip_item.set_anchor_in_parent(
            float(c.art_w) - com_x_art, float(c.art_h) - com_y_art,
        )

"""QWidget adapter for the buddy.

Thin wrapper around ``BuddyCore``. Owns the translucent frameless
window, mouse handling, paint, click-through mask, and the widget
geometry math (sizing the widget to the rotated-art AABB and moving it
so the COM lands on the simulator's world coords). Physics, art
geometry, lerp clock, master sprite, and offscreen rescue all live in
``tokenpal.ui.buddy_core.BuddyCore`` so the QtQuick path can share
them without dragging a hidden widget along.
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QRegion,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.buddy_core import (
    BuddyCore,
    EDGE_DOCK_THRESHOLD as _EDGE_DOCK_THRESHOLD,
    FIXED_DT_S,
    _PHYSICS_DEBUG,
    measure_block_paint_width as _measure_block_paint_width,
)
from tokenpal.ui.qt import _paint_trace
from tokenpal.ui.qt.physics import RigidBodyConfig

# Pixel offsets between the buddy and his attached followers. The
# overlay applies these in screen-axis coords on the QWidget path
# (rotated through `_body_aligned_offset`); the Quick path embeds
# them directly into the pivot-local position so the pivot's
# rotation supplies the body-aligned twist for free.
BUBBLE_HOVER_OFFSET_Y = 16
DOCK_OFFSET_Y = 4


class BuddyWindow(QWidget):
    """Frameless, translucent, always-on-top buddy widget.

    Delegates all model state to a ``BuddyCore`` (signal source for
    ``position_changed``). This widget exists to: own a native window,
    paint the rotated master sprite, take mouse events, and keep its
    pixel size + position in sync with the core's lerped state.
    """

    # Re-exposed for backward compatibility — the canonical signal lives
    # on ``self.core``. Both fire on the same pump.
    position_changed = Signal()

    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        physics_config: RigidBodyConfig | None = None,
        font_family: str = "Courier",
        font_size: int = 14,
    ) -> None:
        super().__init__()
        self._core = BuddyCore(
            frame_lines=frame_lines,
            initial_anchor=initial_anchor,
            physics_config=physics_config,
            font_family=font_family,
            font_size=font_size,
            parent=self,
        )

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Qt.Tool on macOS maps to NSWindow utility-panel behavior that
        # hides the window whenever the app loses focus. On Windows /
        # Linux it's the right flag for "don't show in taskbar"; on
        # macOS we rely on apply_macos_accessory_mode + the NSWindow
        # collectionBehavior tweak applied after the window is native.
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Where the COM sits inside the widget after the rotated-AABB
        # padding. Set by ``_recompute_geometry``.
        self._com_widget: tuple[int, int] = (0, 0)
        self._last_mask_rect: QRect = QRect()

        # Drive widget geometry + paint off the core's tick. This emits
        # the wrapper signal too so existing overlay connections
        # (``self._buddy.position_changed.connect(...)``) keep working.
        self._core.position_changed.connect(self._on_core_tick)

        self._recompute_geometry()
        self._move_to_com()

    # --- Public API ---------------------------------------------------

    @property
    def core(self) -> BuddyCore:
        return self._core

    @property
    def sim(self):  # type: ignore[no-untyped-def]
        return self._core.sim

    def set_right_click_handler(
        self, handler: Callable[[QPoint], None] | None,
    ) -> None:
        self._core.set_right_click_handler(handler)

    def set_frame(self, lines: list[str]) -> None:
        self._core.set_frame(lines)

    def set_zoom(self, factor: float) -> None:
        self._core.set_zoom(factor)

    def head_world_position(self) -> QPointF:
        return self._core.head_world_position()

    def foot_world_position(self) -> QPointF:
        return self._core.foot_world_position()

    def body_angle(self) -> float:
        return self._core.body_angle()

    def art_frame_point_world(self, ax: float, ay: float) -> QPointF:
        return self._core.art_frame_point_world(ax, ay)

    def world_to_art(self, world_point: QPointF) -> QPointF | None:
        return self._core.world_to_art(world_point)

    def art_bounds(self) -> QRect:
        return self._core.art_bounds()

    def buddy_occlusion_rect_world(self) -> QRect:
        return self._core.buddy_occlusion_rect_world()

    def is_painted_cell_at(self, ax: float, ay: float) -> bool:
        return self._core.is_painted_cell_at(ax, ay)

    def is_dragging(self) -> bool:
        return self._core.is_dragging()

    def needs_rotated_followers(self) -> bool:
        return self._core.needs_rotated_followers()

    # --- Event hooks --------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:
        """Apply the click-through mask once the native window has been
        mapped. Calling ``setMask`` before show was leaving the widget
        with a stuck region on macOS — pointer hits ignored for seconds
        after launch until some later event re-applied a fresh mask."""
        super().showEvent(event)
        self._update_click_mask()

    def _on_core_tick(self) -> None:
        # Skip rescue while hidden — no point moving an invisible buddy.
        if not self.isVisible():
            self._core.cancel_offscreen_rescue()
        self._recompute_geometry()
        self._move_to_com()
        self._update_click_mask()
        # repaint() (synchronous) instead of update() so every pump's
        # paint lands in the same Qt event-loop iteration as the
        # followers' synchronous paints driven off position_changed.
        self.repaint()
        self.position_changed.emit()

    # --- Geometry -----------------------------------------------------

    def _recompute_geometry(self) -> None:
        """Size the widget to the AABB of the rotated art at the
        current body angle (plus a slack for paint-time θ
        extrapolation), then place ``_com_widget`` so the body's COM
        lands at the right spot inside the widget.

        Slack covers α=2 (one full extra step of forward extrapolation)
        plus position-lerp residual, so the widget rect always contains
        anything ``_build_transform`` could paint."""
        c = self._core
        com_x, com_y = c.com_art()
        delta_theta = c.sim.theta - c.theta_prev
        if delta_theta > math.pi:
            delta_theta -= 2.0 * math.pi
        elif delta_theta < -math.pi:
            delta_theta += 2.0 * math.pi
        angles = (
            c.theta_prev,
            c.sim.theta + delta_theta,  # α=2 forward extrapolation
        )
        corners: list[QPointF] = []
        for angle in angles:
            rot = QTransform()
            rot.translate(com_x, com_y)
            rot.rotate(math.degrees(angle))
            rot.translate(-com_x, -com_y)
            corners.append(rot.map(QPointF(0.0, 0.0)))
            corners.append(rot.map(QPointF(float(c.art_w), 0.0)))
            corners.append(rot.map(QPointF(0.0, float(c.art_h))))
            corners.append(rot.map(
                QPointF(float(c.art_w), float(c.art_h)),
            ))
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        # 1 px slack for AA glyph edges + extra slack for the
        # position-lerp residual baked into _build_transform.
        vx, vy = c.sim.velocity
        pos_slack = int(math.ceil(max(abs(vx), abs(vy)) * FIXED_DT_S)) + 1
        min_x = int(math.floor(min(xs))) - 1 - pos_slack
        min_y = int(math.floor(min(ys))) - 1 - pos_slack
        max_x = int(math.ceil(max(xs))) + 1 + pos_slack
        max_y = int(math.ceil(max(ys))) + 1 + pos_slack
        width = max(max_x - min_x, 1)
        height = max(max_y - min_y, 1)
        self._com_widget = (int(com_x) - min_x, int(com_y) - min_y)
        # Skip the WM resize when nothing changed — even a no-op resize
        # dispatches WM_SIZE on Windows and costs ~1 ms.
        if self.size().width() != width or self.size().height() != height:
            self.resize(width, height)

    def _move_to_com(self) -> None:
        cx, cy = self._core.sim.position
        new_x = int(cx) - self._com_widget[0]
        new_y = int(cy) - self._com_widget[1]
        old_pos = self.pos()
        if new_x != old_pos.x() or new_y != old_pos.y():
            self.move(new_x, new_y)

    def _update_click_mask(self) -> None:
        """Mask the buddy to its widget rect so clicks fall through to
        whatever's behind any antialias slack. ``_recompute_geometry``
        already sized the widget to the tightest legal mask.

        Skip the setMask call when the widget rect hasn't changed —
        on Windows the round-trip costs ~1 ms."""
        rect = self.rect()
        if rect == self._last_mask_rect:
            return
        self.setMask(QRegion(rect))
        self._last_mask_rect = rect

    def _build_transform(self) -> QTransform:
        """Forward art-frame → widget-frame transform. Painted COM
        lands at the continuous lerped position, not at
        ``int(sim.position)`` — the latter judders by up to one physics
        step's worth of motion per paint, scaled by zoom."""
        theta, cx_lerp, cy_lerp = self._core.lerped_state()
        widget_pos = self.pos()
        sub_x = cx_lerp - widget_pos.x() - self._com_widget[0]
        sub_y = cy_lerp - widget_pos.y() - self._com_widget[1]

        t = QTransform()
        t.translate(self._com_widget[0] + sub_x, self._com_widget[1] + sub_y)
        t.rotate(math.degrees(theta))
        com_x, com_y = self._core.com_art()
        t.translate(-com_x, -com_y)
        return t

    # --- Render -------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        # WA_TranslucentBackground already clears the region; no
        # fillRect needed.
        painter = QPainter(self)
        # Bilinear sample of the master pixmap through the world
        # transform — same op as a game engine drawing a sprite quad.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setWorldTransform(self._build_transform())
        c = self._core
        painter.drawPixmap(
            QRect(0, 0, c.art_w, c.art_h),
            c.render_art_pixmap(self.devicePixelRatioF()),
        )
        if _paint_trace.enabled():
            theta, cx, cy = c.lerped_state()
            _paint_trace.log_paint("buddy", theta=theta, pos=(cx, cy))

        if _PHYSICS_DEBUG:
            painter.setWorldTransform(QTransform())
            c.paint_physics_debug(painter, self._com_widget)

    # --- Input --------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            handler = self._core.right_click_handler
            if handler is not None:
                handler(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        cursor_widget = event.position()
        art_pos = self._invert_widget_to_art(cursor_widget)
        if art_pos is None:
            event.ignore()
            return
        self._core.begin_drag(art_pos, event.globalPosition())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._core.is_dragging():
            return
        cursor = event.globalPosition()
        self._core.set_grab_target(cursor.x(), cursor.y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._core.end_drag()

    def _invert_widget_to_art(self, pos_widget: QPointF) -> QPointF | None:
        """Inverse of ``_build_transform`` + hit-test. Returns the art-
        frame coord for ``pos_widget`` if it lands on the art, else
        ``None``."""
        transform = self._build_transform()
        inv, invertible = transform.inverted()
        if not invertible:
            return None
        art = inv.map(pos_widget)
        c = self._core
        if 0.0 <= art.x() <= c.art_w and 0.0 <= art.y() <= c.art_h:
            return art
        return None

    # --- Lifecycle ----------------------------------------------------

    def closeEvent(self, event):  # type: ignore[no-untyped-def]
        self._core.sleep_tick_timer()
        super().closeEvent(event)

    # --- Backward-compat private-attr shims ----------------------------
    # The Quick path and tests reach into ``_sim``, ``_wake_timer``,
    # etc. directly. After all callers migrate to the public BuddyCore
    # API these can be deleted; for now they keep the QWidget surface
    # source-compatible with the pre-split BuddyWindow.

    @property
    def _sim(self):  # type: ignore[no-untyped-def]
        return self._core.sim

    @property
    def _art_w(self) -> int:
        return self._core.art_w

    @property
    def _art_h(self) -> int:
        return self._core.art_h

    @property
    def _cell_w(self) -> int:
        return self._core.cell_w

    @property
    def _line_h(self) -> int:
        return self._core.line_h

    @property
    def _zoom(self) -> float:
        return self._core.zoom

    @property
    def _font(self):  # type: ignore[no-untyped-def]
        return self._core._font

    @property
    def _frame_lines(self) -> list[str]:
        return self._core.frame_lines

    @property
    def _paint_target_ts(self) -> float | None:
        return self._core.paint_target_ts

    @property
    def _last_step_ts(self) -> float:
        return self._core.last_step_ts

    @property
    def _theta_prev(self) -> float:
        return self._core.theta_prev

    @property
    def _pos_prev(self) -> tuple[float, float]:
        return self._core.pos_prev

    @property
    def _drag_active(self) -> bool:
        return self._core.is_dragging()

    @_drag_active.setter
    def _drag_active(self, value: bool) -> None:
        self._core._drag_active = value

    @property
    def _on_right_click(self):  # type: ignore[no-untyped-def]
        return self._core.right_click_handler

    @property
    def _timer(self):  # type: ignore[no-untyped-def]
        return self._core._timer

    def _com_art(self) -> tuple[float, float]:
        return self._core.com_art()

    def _render_art_pixmap(self):  # type: ignore[no-untyped-def]
        return self._core.render_art_pixmap(self.devicePixelRatioF())

    def _on_tick(self) -> None:
        self._core._on_tick()

    def _wake_timer(self) -> None:
        self._core.wake_tick_timer()

    def _sleep_timer(self) -> None:
        self._core.sleep_tick_timer()

    def _maybe_edge_dock(self) -> None:
        self._core.maybe_edge_dock()

    def _begin_drag(
        self, art_pos: QPointF, cursor_global: QPointF,
    ) -> None:
        self._core.begin_drag(art_pos, cursor_global)

    def _refresh_view(self) -> None:
        """Compatibility shim — pre-split this resized + repainted in
        one call. Now triggered by the core's tick signal; callers
        forcing a manual refresh still work."""
        self._on_core_tick()


# Re-export for callers reaching into the QWidget module for these
# constants (e.g. the Quick path's _clamped_lerp helper).
_FIXED_DT_S = FIXED_DT_S

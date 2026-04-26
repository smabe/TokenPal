"""Frameless, transparent, always-on-top buddy window.

The buddy is a 2D rigid body whose COM is held near a screen anchor
(``home``) by a soft spring-damper. When the user grabs him, a soft
mouse-joint constraint at the body-local grab point drives that anchor
toward the cursor; both linear and angular motion fall out of the same
impulse, so off-COM grabs naturally rotate more than near-COM grabs.
On release the constraint is dropped and the home spring catches the
body.

Physics lives in ``tokenpal.ui.qt.physics.RigidBodySimulator``; this
module owns the Qt plumbing (drag input, 60 Hz tick, paint-time
rotation, widget sizing for the rotated art).
"""

from __future__ import annotations

import dataclasses
import math
import os
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QRegion,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.palette import BUDDY_GREEN
from tokenpal.ui.qt._screen_bounds import Rect, offscreen_rescue_target
from tokenpal.ui.qt.markup import parse_markup, stripped_text
from tokenpal.ui.qt.physics import RigidBodyConfig, RigidBodySimulator

_PHYSICS_HZ = 60
_TICK_MS = int(1000 / _PHYSICS_HZ)
_EDGE_DOCK_THRESHOLD = 20       # px from screen edge triggers snap
# 2 s grace lets a fling overshoot and bounce back via momentum before
# the rescue stomps in.
_OFFSCREEN_RESCUE_DELAY_S = 2.0
_OFFSCREEN_RESCUE_DURATION_S = 0.5
_OFFSCREEN_RESCUE_INSET = 24
# Head-heavy center of mass. y-fraction down from the crown. 0.30 puts
# the COM in the upper torso so a foot-grab flops the head down (heavier
# end falls) and the buddy has a preferred orientation when released.
_COM_Y_FRACTION = 0.30

# Thresholds below which followers (bubble, dock) revert to their
# unrotated form. Anything smaller is indistinguishable from rest.
_FOLLOWER_ROTATION_EPS = 0.01
_FOLLOWER_OMEGA_EPS = 0.1

# Enable an on-screen HUD + file log of body state by setting
# TOKENPAL_PHYSICS_DEBUG=1 in the environment before launch.
_PHYSICS_DEBUG = bool(os.environ.get("TOKENPAL_PHYSICS_DEBUG"))
_PHYSICS_DEBUG_LOG_EVERY = 3
_PHYSICS_DEBUG_LOG_PATH = "/tmp/tokenpal-physics.log"

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

    Rotation is around the body's center of mass; ``sim.theta = 0``
    means upright. Grab anywhere on the art and a soft constraint
    pulls that body-local point toward the cursor — off-COM grabs
    rotate more, COM grabs translate cleanly. Release drops the
    constraint; the home spring pulls the body back upright.
    """

    # Fires whenever the body physically moves — either because the
    # physics tick advanced the simulator or because ``set_frame``
    # resized the window. Followers (bubble, dock) use this to track
    # the buddy across the screen.
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

        # --- Art geometry. See _recompute_geometry for the model. ---
        self._cols = 0
        self._cell_w = 1
        self._line_h = 1
        self._art_w = 1
        self._art_h = 1
        # Where the COM sits inside the widget after the rotated-AABB
        # padding. Set by _recompute_geometry; equals the COM's
        # screen-space offset from the widget's top-left.
        self._com_widget: tuple[int, int] = (0, 0)

        self._measure_cells()

        cfg = physics_config or RigidBodyConfig()
        cfg = dataclasses.replace(cfg, inertia=self._compute_inertia(cfg.mass))
        self._sim = RigidBodySimulator(home=initial_anchor, config=cfg)

        # _recompute_geometry reads sim.theta, so the sim must exist first.
        self._recompute_geometry()
        self._move_to_com()

        self._drag_active = False

        self._offscreen_since: float | None = None
        self._rescue_t0: float | None = None
        self._rescue_start: tuple[float, float] = (0.0, 0.0)
        self._rescue_target: tuple[float, float] = (0.0, 0.0)

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._last_tick_ts = time.monotonic()
        # Start settled — the simulator wakes on grab / impulse.

        self._debug_tick_counter = 0
        self._debug_log_fp = None
        if _PHYSICS_DEBUG:
            self._debug_log_fp = open(  # noqa: SIM115 — lifetime = widget
                _PHYSICS_DEBUG_LOG_PATH, "w", buffering=1,
            )

    def set_right_click_handler(
        self, handler: Callable[[QPoint], None] | None,
    ) -> None:
        self._on_right_click = handler

    def showEvent(self, event: QShowEvent) -> None:
        """Apply the click-through mask once the native window has been
        mapped. Calling ``setMask`` in ``__init__`` (before show) was
        leaving the widget with a stuck region on macOS — pointer hits
        were being ignored for seconds after launch until some later
        event (voice frame load, tick) re-applied a fresh mask."""
        super().showEvent(event)
        self._update_click_mask()

    def set_frame(self, lines: list[str]) -> None:
        self._frame_lines = list(lines)
        self._measure_cells()
        # Voice/pose frame swaps don't change art bounds enough to
        # justify rebuilding inertia. Body keeps its initial moment.
        self._refresh_view()

    # --- Geometry --------------------------------------------------------

    def _measure_cells(self) -> None:
        """Compute per-cell dimensions and art bounding box from the font
        and frame lines."""
        self._font_init_metrics()
        cols = max(
            (len(stripped_text(line)) for line in self._frame_lines),
            default=0,
        )
        self._cols = cols
        self._art_w = max(cols * self._cell_w, 1)
        self._art_h = max(self._line_h * len(self._frame_lines), 1)

    def _font_init_metrics(self) -> None:
        """Recompute ``_cell_w`` and ``_line_h`` from the current font.

        Treat the art as a fixed-pitch grid rather than trusting
        ``horizontalAdvance`` on the whole line. Menlo's reported advance
        for block glyphs (U+2588 and friends) includes side bearings the
        painter doesn't actually draw — stepping exactly at the reported
        advance leaves 1-pixel seams between blocks. We step by the
        glyph's painted width minus one so neighbours merge into a solid
        field. Row step uses ``ascent()`` instead of ``height()`` because
        block art never descends below the baseline; the extra padding
        just opens gaps between stacked blocks.
        """
        fm = self.fontMetrics()
        self._cell_w = max(_measure_block_paint_width(self._font) - 1, 1)
        self._line_h = fm.ascent()

    def _com_art(self) -> tuple[float, float]:
        """Center of mass in art-frame coords. Head-heavy (see
        ``_COM_Y_FRACTION``), not geometric center — biases the
        body's preferred orientation when released."""
        return (self._art_w / 2.0, self._art_h * _COM_Y_FRACTION)

    def _compute_inertia(self, mass: float) -> float:
        """Approximate the body as a uniform disk of radius ``R``, with
        ``R`` = farthest corner distance from COM. ``I = m·R²/2``. Far
        from physically true for a head-heavy ASCII figure, but the
        constraint Jacobian uses I only as a relative weight against
        ``m``, so the absolute number just sets feel — and feel is
        tuned via the Hz dials anyway."""
        com_x, com_y = self._com_art()
        r = math.hypot(
            max(com_x, self._art_w - com_x),
            max(com_y, self._art_h - com_y),
        )
        return max(mass * r * r * 0.5, 1.0)

    def _recompute_geometry(self) -> None:
        """Size the widget to the AABB of the rotated art at the current
        body angle, then place ``_com_widget`` so the body's COM lands
        at the right spot inside the widget."""
        angle_deg = math.degrees(self._sim.theta)
        com_x, com_y = self._com_art()
        rot = QTransform()
        rot.translate(com_x, com_y)
        rot.rotate(angle_deg)
        rot.translate(-com_x, -com_y)
        corners = (
            rot.map(QPointF(0.0, 0.0)),
            rot.map(QPointF(float(self._art_w), 0.0)),
            rot.map(QPointF(0.0, float(self._art_h))),
            rot.map(QPointF(float(self._art_w), float(self._art_h))),
        )
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        # 1 px slack so antialiased glyph edges at AABB corners don't
        # clip during rotation. Same convention as the click mask.
        min_x = int(math.floor(min(xs))) - 1
        min_y = int(math.floor(min(ys))) - 1
        max_x = int(math.ceil(max(xs))) + 1
        max_y = int(math.ceil(max(ys))) + 1
        width = max(max_x - min_x, 1)
        height = max(max_y - min_y, 1)
        self._com_widget = (int(com_x) - min_x, int(com_y) - min_y)
        self.resize(width, height)

    def _move_to_com(self) -> None:
        """Position the widget so ``_com_widget`` lands on the
        simulator's COM in global screen coords. Emits
        ``position_changed`` so followers (bubble, dock) reposition."""
        cx, cy = self._sim.position
        new_x = int(cx) - self._com_widget[0]
        new_y = int(cy) - self._com_widget[1]
        old_pos = self.pos()
        if new_x != old_pos.x() or new_y != old_pos.y():
            self.move(new_x, new_y)
        # Always emit while the tick timer is running. Followers need
        # the "now at rest" update on the settle tick to swap their
        # rotated/mock form back to the interactive one.
        self.position_changed.emit()

    def head_world_position(self) -> QPointF:
        """Rotated head-anchor (top-center of original art) in global
        screen coords. Speech bubble follows this through a swing."""
        return self._art_point_world(self._art_w / 2.0, 0.0)

    def foot_world_position(self) -> QPointF:
        """Rotated foot-anchor (bottom-center of original art) in
        global screen coords. The chat dock hangs under this."""
        return self._art_point_world(self._art_w / 2.0, float(self._art_h))

    def body_angle(self) -> float:
        """Total rotation of the body in the world frame, in radians.
        Followers use this to rotate with the buddy so they stay
        rigidly attached during a swing."""
        return self._sim.theta

    def art_frame_point_world(self, ax: float, ay: float) -> QPointF:
        """Map an art-frame coord (may be outside the art bounds) to
        its world position under the current body rotation."""
        return self._art_point_world(ax, ay)

    def buddy_occlusion_rect_world(self) -> QRect:
        """Axis-aligned screen-space bounding box of the rotated art."""
        widget_pos = self.pos()
        t = self._build_transform()
        corners = (
            t.map(QPointF(0.0, 0.0)),
            t.map(QPointF(float(self._art_w), 0.0)),
            t.map(QPointF(0.0, float(self._art_h))),
            t.map(QPointF(float(self._art_w), float(self._art_h))),
        )
        xs = [widget_pos.x() + p.x() for p in corners]
        ys = [widget_pos.y() + p.y() for p in corners]
        x = int(math.floor(min(xs))) - 1
        y = int(math.floor(min(ys))) - 1
        x2 = int(math.ceil(max(xs))) + 1
        y2 = int(math.ceil(max(ys))) + 1
        return QRect(x, y, max(x2 - x, 1), max(y2 - y, 1))

    def world_to_art(self, world_point: QPointF) -> QPointF | None:
        """Inverse of ``_art_point_world``."""
        widget_pos = self.pos()
        local = QPointF(
            world_point.x() - widget_pos.x(),
            world_point.y() - widget_pos.y(),
        )
        inv, ok = self._build_transform().inverted()
        if not ok:
            return None
        return inv.map(local)

    def art_bounds(self) -> QRect:
        """Tight art-frame AABB: ``(0, 0) → (art_w, art_h)``."""
        return QRect(0, 0, self._art_w, self._art_h)

    def is_painted_cell_at(self, ax: float, ay: float) -> bool:
        """Hit-test an art-frame point against the actually-painted
        glyphs (not just the AABB). Mirrors ``paintEvent``'s per-line
        horizontal centering so weather flakes only collide with cells
        that have a non-space glyph."""
        if ay < 0.0 or ax < 0.0:
            return False
        row = int(ay // self._line_h)
        if row < 0 or row >= len(self._frame_lines):
            return False
        text = stripped_text(self._frame_lines[row])
        chars = len(text)
        if chars == 0:
            return False
        base_x = (self._art_w - chars * self._cell_w) // 2
        col = int((ax - base_x) // self._cell_w)
        if col < 0 or col >= chars:
            return False
        return text[col] != " "

    def is_dragging(self) -> bool:
        return self._drag_active

    def needs_rotated_followers(self) -> bool:
        """True when followers should render their rotated/mock form."""
        return (
            abs(self._sim.theta) > _FOLLOWER_ROTATION_EPS
            or abs(self._sim.omega) > _FOLLOWER_OMEGA_EPS
        )

    def _art_point_world(self, ax: float, ay: float) -> QPointF:
        widget_pos = self.pos()
        p_widget = self._build_transform().map(QPointF(ax, ay))
        return QPointF(
            widget_pos.x() + p_widget.x(),
            widget_pos.y() + p_widget.y(),
        )

    def _build_transform(self) -> QTransform:
        """Forward art-frame → widget-frame transform: translate art
        origin to ``-com_art``, rotate by ``sim.theta``, translate to
        ``com_widget``. ``paintEvent`` applies the same ops via
        ``painter.translate/rotate`` so hit-test stays in sync."""
        t = QTransform()
        t.translate(self._com_widget[0], self._com_widget[1])
        t.rotate(math.degrees(self._sim.theta))
        com_x, com_y = self._com_art()
        t.translate(-com_x, -com_y)
        return t

    def _update_click_mask(self) -> None:
        """Mask the buddy to its widget rect so clicks fall through to
        whatever's behind any antialias slack. Since
        ``_recompute_geometry`` sizes the widget to the rotated-art
        AABB, the widget rect is already the tightest legal mask."""
        self.setMask(QRegion(self.rect()))

    def _refresh_view(self) -> None:
        """Resize the widget to the rotated-art AABB, position it on
        the COM, refresh the click-through mask, and request a repaint."""
        self._recompute_geometry()
        self._move_to_com()
        self._update_click_mask()
        self.update()

    # --- Tick / timer ---------------------------------------------------

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick_ts
        self._last_tick_ts = now
        # Clamp dt to survive timer stalls — soft-constraint γ/β are
        # dt-dependent and a 200ms hiccup would over-correct visibly.
        self._sim.tick(min(dt, 1.0 / 30.0))
        self._tick_offscreen_rescue(now)
        self._refresh_view()
        if _PHYSICS_DEBUG:
            self._debug_tick_counter += 1
            if self._debug_tick_counter % _PHYSICS_DEBUG_LOG_EVERY == 0:
                self._log_physics_debug()
        rescue_pending = (
            self._offscreen_since is not None or self._rescue_t0 is not None
        )
        if self._sim.sleeping and not self._drag_active and not rescue_pending:
            self._sleep_timer()

    def _wake_timer(self) -> None:
        if not self._timer.isActive():
            self._last_tick_ts = time.monotonic()
            self._timer.start()

    def _sleep_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    # --- Off-screen rescue ----------------------------------------------

    def _screen_rects(self) -> list[Rect]:
        rects: list[Rect] = []
        for screen in QGuiApplication.screens():
            geom = screen.availableGeometry()
            rects.append((geom.left(), geom.top(), geom.right(), geom.bottom()))
        return rects

    def _tick_offscreen_rescue(self, now: float) -> None:
        """Watch for the COM staying off every screen and tween it back.

        Skipped during a drag (user is in control) and while hidden (no
        point rescuing an invisible buddy). The tween itself drives
        ``snap_home`` per tick, which zeroes velocity so residual fling
        momentum doesn't fight the slide back.
        """
        if self._drag_active or not self.isVisible():
            self._offscreen_since = None
            self._rescue_t0 = None
            return

        if self._rescue_t0 is not None:
            elapsed = now - self._rescue_t0
            t = min(1.0, elapsed / _OFFSCREEN_RESCUE_DURATION_S)
            ease = t * t * (3.0 - 2.0 * t)
            sx, sy = self._rescue_start
            tx, ty = self._rescue_target
            x = sx + (tx - sx) * ease
            y = sy + (ty - sy) * ease
            self._sim.snap_home(x, y)
            if t >= 1.0:
                self._rescue_t0 = None
            return

        target = offscreen_rescue_target(
            self._sim.position, self._screen_rects(), _OFFSCREEN_RESCUE_INSET,
        )
        if target is None:
            self._offscreen_since = None
            return
        if self._offscreen_since is None:
            self._offscreen_since = now
            return
        if now - self._offscreen_since >= _OFFSCREEN_RESCUE_DELAY_S:
            self._offscreen_since = None
            self._rescue_start = self._sim.position
            self._rescue_target = target
            self._rescue_t0 = now

    def _cancel_offscreen_rescue(self) -> None:
        self._offscreen_since = None
        self._rescue_t0 = None

    # --- Input -----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if self._on_right_click is not None:
                self._on_right_click(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Identify the art pixel under the cursor. If the click landed
        # in transparent padding (widget is 2-3× the art), ignore it so
        # the user can click through to windows behind the buddy.
        cursor_widget = event.position()
        art_pos = self._invert_widget_to_art(cursor_widget)
        if art_pos is None:
            event.ignore()
            return

        cursor_global = event.globalPosition()
        self._begin_drag(art_pos, cursor_global)

    def _begin_drag(
        self,
        art_pos: QPointF,
        cursor_global: QPointF,
    ) -> None:
        """Start a grab. Convert the art-frame click point to a body-
        local offset (relative to COM) and hand it to the simulator
        as the constraint anchor."""
        self._cancel_offscreen_rescue()
        com_x, com_y = self._com_art()
        local_x = art_pos.x() - com_x
        local_y = art_pos.y() - com_y
        # Rotate the local offset *back* into body frame: the click
        # landed on an art point that's currently rotated by sim.theta,
        # so the body-frame anchor is the inverse-rotated offset.
        cos_t = math.cos(self._sim.theta)
        sin_t = math.sin(self._sim.theta)
        body_local_x = cos_t * local_x + sin_t * local_y
        body_local_y = -sin_t * local_x + cos_t * local_y
        self._sim.begin_grab(
            local_x=body_local_x,
            local_y=body_local_y,
            target_x=cursor_global.x(),
            target_y=cursor_global.y(),
        )
        self._drag_active = True
        self._wake_timer()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_active:
            return
        cursor = event.globalPosition()
        self._sim.set_grab_target(cursor.x(), cursor.y())
        self._wake_timer()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._drag_active:
            return
        self._drag_active = False
        # Drop the constraint. Body coasts with whatever (v, ω) the
        # constraint solver imparted during the drag — no separate
        # fling impulse, the velocity already lives in the state.
        self._sim.end_grab()
        self._maybe_edge_dock()
        # Keep the timer running — the body may still be swinging
        # toward home under the soft spring.

    def _invert_widget_to_art(self, pos_widget: QPointF) -> QPointF | None:
        """Inverse of ``_build_transform`` + hit-test. Returns the art-
        frame coord for ``pos_widget`` if it lands on the art, else
        ``None``."""
        transform = self._build_transform()
        inv, invertible = transform.inverted()
        if not invertible:
            return None
        art = inv.map(pos_widget)
        if 0.0 <= art.x() <= self._art_w and 0.0 <= art.y() <= self._art_h:
            return art
        return None

    def _maybe_edge_dock(self) -> None:
        """Snap the home anchor to the nearest screen edge when the
        buddy's COM is dropped close to one. Keeps the buddy 'sticky'
        to monitor boundaries; multi-monitor handled via QScreen lookup
        at the COM's current position."""
        cx, cy = self._sim.position
        screen = QGuiApplication.screenAt(QPoint(int(cx), int(cy)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        new_x, new_y = cx, cy
        if cx - geom.left() < _EDGE_DOCK_THRESHOLD:
            new_x = float(geom.left())
        elif geom.right() - cx < _EDGE_DOCK_THRESHOLD:
            new_x = float(geom.right())
        if cy - geom.top() < _EDGE_DOCK_THRESHOLD:
            new_y = float(geom.top())
        elif geom.bottom() - cy < _EDGE_DOCK_THRESHOLD:
            new_y = float(geom.bottom())
        if (new_x, new_y) != (cx, cy):
            # Teleport-snap to the edge: the new model has no linear
            # home spring, so ``set_home`` would just relocate the
            # docking target without pulling the buddy there. Since
            # edge-dock only fires when the user has clearly released
            # near an edge, snapping is the intent — the buddy's
            # residual slide momentum is acceptably forfeit.
            self._sim.snap_home(new_x, new_y)

    # --- Render ----------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        # WA_TranslucentBackground already clears the region to fully
        # transparent before this runs, so no fillRect needed.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)
        # Apply the same transform the hit-test uses, so what's painted
        # and what can be clicked agree.
        painter.setWorldTransform(self._build_transform())

        fm = self.fontMetrics()
        line_h = self._line_h
        cell_w = self._cell_w
        y = fm.ascent()
        total_w = self._cols * cell_w
        for line in self._frame_lines:
            chars = len(stripped_text(line))
            base_x = (total_w - chars * cell_w) // 2
            col = 0
            for seg in parse_markup(line):
                painter.setPen(QColor(seg.color) if seg.color else _FG_COLOR)
                for ch in seg.text:
                    painter.drawText(base_x + col * cell_w, y, ch)
                    col += 1
            y += line_h

        if _PHYSICS_DEBUG:
            # Debug HUD paints on top of the rotated art but in widget-
            # local coords, so the text stays upright regardless of θ.
            painter.setWorldTransform(QTransform())
            self._paint_physics_debug(painter)

    # --- Physics debug HUD ----------------------------------------------

    def _paint_physics_debug(self, painter: QPainter) -> None:
        sim = self._sim
        cwx, cwy = self._com_widget
        # Magenta crosshair at the COM.
        pen = QColor("magenta")
        painter.setPen(pen)
        painter.drawLine(cwx - 12, cwy, cwx + 12, cwy)
        painter.drawLine(cwx, cwy - 12, cwx, cwy + 12)

        cursor = QCursor.pos()
        vx, vy = sim.velocity
        speed = math.hypot(vx, vy)
        cx, cy = sim.position
        hx, hy = sim.home
        state = (
            "DRAG" if self._drag_active
            else ("SLEEP" if sim.sleeping else "swing")
        )
        lines = [
            f"theta {math.degrees(sim.theta):+6.1f}deg  "
            f"w {sim.omega:+5.2f}rad/s",
            f"COM    ({cx:6.0f}, {cy:6.0f})",
            f"home   ({hx:6.0f}, {hy:6.0f})",
            f"cursor ({cursor.x():6d}, {cursor.y():6d})",
            f"|v|    {speed:5.0f} px/s  ({vx:+5.0f}, {vy:+5.0f})",
            f"state  {state}",
        ]
        debug_font = QFont("Menlo", 9)
        debug_font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(debug_font)
        fm = painter.fontMetrics()
        line_h = fm.height()
        text_x = cwx + 16
        text_y = cwy + 16 + fm.ascent()
        backdrop = QColor(0, 0, 0, 160)
        longest = max(fm.horizontalAdvance(line) for line in lines)
        painter.fillRect(
            text_x - 4, text_y - fm.ascent() - 2,
            longest + 8, line_h * len(lines) + 4,
            backdrop,
        )
        painter.setPen(QColor("#ff66cc"))
        for line in lines:
            painter.drawText(text_x, text_y, line)
            text_y += line_h

    def _log_physics_debug(self) -> None:
        if self._debug_log_fp is None:
            return
        sim = self._sim
        vx, vy = sim.velocity
        cursor = QCursor.pos()
        state = (
            "DRAG" if self._drag_active
            else ("SLEEP" if sim.sleeping else "swing")
        )
        self._debug_log_fp.write(
            f"t={time.monotonic():.3f} "
            f"theta={math.degrees(sim.theta):+7.2f} "
            f"w={sim.omega:+6.3f} "
            f"COM=({sim.position[0]:.1f},{sim.position[1]:.1f}) "
            f"home=({sim.home[0]:.1f},{sim.home[1]:.1f}) "
            f"cursor=({cursor.x()},{cursor.y()}) "
            f"v=({vx:+.1f},{vy:+.1f}) "
            f"{state}\n",
        )

"""Backend-agnostic core of the buddy.

Owns the physics simulator, art geometry, master sprite, lerp clock,
mouse-grab state, and offscreen rescue. No QWidget / no QQuickItem —
just a ``QObject`` so we can carry a ``QTimer`` and a
``position_changed`` signal that both the QWidget and QtQuick paths
consume. Backends supply their own paint surface and mouse events; the
core supplies the model state.

World coords throughout (no widget-local pixel offsets). The QWidget
adapter lives in ``tokenpal.ui.qt.buddy_window``; the Quick host in
``tokenpal.ui.quick.buddy_window``.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPixmap,
)

from tokenpal.ui.palette import BUDDY_GREEN
from tokenpal.ui.qt._screen_bounds import Rect, offscreen_rescue_target
from tokenpal.ui.qt._text_fx import paint_block_char, scale_font
from tokenpal.ui.qt.markup import parse_markup, stripped_text
from tokenpal.ui.qt.physics import RigidBodyConfig, RigidBodySimulator

log = logging.getLogger(__name__)

# Pump rate for the accumulator loop. Qt's PreciseTimer floor on
# Windows 11 is ~6 ms regardless of setInterval (multimedia-timer
# dispatch jitter), so asking for less is wasted.
TICK_INTERVAL_MS = 6
# Fixed physics step. Decoupled from the pump rate: each pump call
# accumulates wall-clock time and runs as many ``FIXED_DT`` integrations
# as needed to drain the accumulator. Matches Glenn Fiedler's "Fix Your
# Timestep" — physics state advances by an invariant dt every step,
# eliminating dt-jitter as a source of integration noise. 240 Hz is
# above every common display refresh so the accumulator drains in
# 1-2 steps per pump regardless of vsync rate.
FIXED_DT_S = 1.0 / 240.0
# Cap accumulated time to avoid the death-spiral failure mode (a long
# stall building up many seconds of physics to catch up on, which then
# blows the next pump's budget and stalls again).
_MAX_FRAME_TIME_S = 0.25
EDGE_DOCK_THRESHOLD = 20       # px from screen edge triggers snap
# 2 s grace lets a fling overshoot and bounce back via momentum before
# the rescue stomps in.
OFFSCREEN_RESCUE_DELAY_S = 2.0
OFFSCREEN_RESCUE_DURATION_S = 0.5
OFFSCREEN_RESCUE_INSET = 24
# Head-heavy center of mass. y-fraction down from the crown. 0.30 puts
# the COM in the upper torso so a foot-grab flops the head down (heavier
# end falls) and the buddy has a preferred orientation when released.
COM_Y_FRACTION = 0.30

# Thresholds below which followers (bubble, dock) revert to their
# unrotated form. Anything smaller is indistinguishable from rest.
_FOLLOWER_ROTATION_EPS = 0.01
_FOLLOWER_OMEGA_EPS = 0.1

# Reference zoom factor at which the invariant master sprite is
# rasterized. paintEvent's drawPixmap implicitly scales to the current
# zoom, so the master stays crisp from 0.5× up to roughly this value;
# beyond it bilinear sampling starts to read soft.
_MASTER_ZOOM = 2.0
_MASTER_SUPERSAMPLE = 2

_PHYSICS_DEBUG = bool(os.environ.get("TOKENPAL_PHYSICS_DEBUG"))
_PHYSICS_DEBUG_LOG_EVERY = 3
_PHYSICS_DEBUG_LOG_PATH = "/tmp/tokenpal-physics.log"

_TICK_PROFILE = bool(os.environ.get("TOKENPAL_TICK_PROFILE"))

_FG_COLOR = QColor(BUDDY_GREEN)


_BLOCK_PAINT_WIDTH_CACHE: dict[tuple[str, int], int] = {}


def measure_block_paint_width(font: QFont) -> int:
    """Return the width in pixels that a single U+2588 FULL BLOCK glyph
    actually paints with ``font``. Cached by (family, pointSize) so
    drag-to-zoom doesn't re-rasterize the probe glyph every tick."""
    key = (font.family(), font.pointSize())
    cached = _BLOCK_PAINT_WIDTH_CACHE.get(key)
    if cached is not None:
        return cached
    img = QImage(64, 32, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    painter.setFont(font)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
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
        result = max(1, int(font.pointSize() * 0.7))
    else:
        result = max(1, hi - lo + 1)
    _BLOCK_PAINT_WIDTH_CACHE[key] = result
    return result


class BuddyCore(QObject):
    """Backend-agnostic buddy state.

    Holds the simulator, art geometry, lerp clock, master sprite cache,
    and mouse-grab / offscreen-rescue state. Emits ``position_changed``
    on every pump and on ``set_frame``. World coords throughout —
    callers (QWidget BuddyWindow / QtQuick BuddyQuickWindow) translate
    to their own local frames.
    """

    # Fires whenever physics advances or the frame swaps. Followers
    # (bubble, dock, resize grip) connect to this to stay attached.
    position_changed = Signal()
    # Fires on transitions in/out of the at-rest state. True when the
    # buddy becomes interactive (drag/rescue/non-sleeping); False when
    # he settles. Backends use this to gate their render loops — on the
    # Quick path, frameSwapped keeps firing at vsync regardless of any
    # timer, so we disconnect it from the physics tick while idle.
    awake_changed = Signal(bool)

    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        physics_config: RigidBodyConfig | None = None,
        font_family: str = "Courier",
        font_size: int = 14,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame_lines = list(frame_lines)
        self._on_right_click: Callable[[QPoint], None] | None = None

        self._base_font = QFont(font_family, font_size)
        self._base_font.setStyleHint(QFont.StyleHint.Monospace)
        # Greyscale AA only; subpixel AA on translucent widgets dots glyphs
        # (QTBUG-43774).
        self._base_font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.NoSubpixelAntialias,
        )
        self._zoom = 1.0
        self._font = QFont(self._base_font)
        # Master sprite font + cell metrics — fixed at _MASTER_ZOOM and
        # never touched after init, so the per-pose cache is zoom-invariant.
        self._master_font = scale_font(self._base_font, _MASTER_ZOOM)
        self._cell_w_master = max(
            measure_block_paint_width(self._master_font) - 1, 1,
        )
        self._line_h_master = QFontMetrics(self._master_font).height()

        # --- Art geometry ---
        self._cols = 0
        self._cell_w = 1
        self._line_h = 1
        self._art_w = 1
        self._art_h = 1
        self._art_pixmap: QPixmap | None = None
        self._pixmap_cache: dict[tuple[object, ...], QPixmap] = {}

        self._measure_cells()

        self._base_physics = physics_config or RigidBodyConfig()
        self._sim = RigidBodySimulator(
            home=initial_anchor, config=self._zoomed_physics_config(),
        )

        # Fix-Your-Timestep state.
        now = time.monotonic()
        self._accumulator = 0.0
        self._theta_prev = self._sim.theta
        self._pos_prev = self._sim.position
        self._last_step_ts = now
        self._last_tick_ts = now
        # Shared paint clock (see _lerped_state).
        self._paint_target_ts: float | None = None

        self._drag_active = False
        self._offscreen_since: float | None = None
        self._rescue_t0: float | None = None
        self._rescue_start: tuple[float, float] = (0.0, 0.0)
        self._rescue_target: tuple[float, float] = (0.0, 0.0)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        self._debug_tick_counter = 0
        self._debug_log_fp = None
        if _PHYSICS_DEBUG:
            self._debug_log_fp = open(  # noqa: SIM115 — lifetime = object
                _PHYSICS_DEBUG_LOG_PATH, "w", buffering=1,
            )

        self._tick_intervals: deque[float] = deque(maxlen=600)
        self._tick_durations: deque[float] = deque(maxlen=600)
        self._tick_profile_last_log = 0.0
        self._tick_profile_last_perf = 0.0

        # Track at-rest state across ticks so position_changed.emit()
        # can no-op while the buddy is fully settled. Without this, the
        # Quick backend's frameSwapped-driven _on_tick fires every
        # vsync forever and the four position_changed slots in the
        # overlay (bubble/dock/grip/rain reanchors) keep dirtying
        # translucent always-on-top widgets, costing real DWM time on
        # iGPU-class hardware.
        self._was_idle = self._sim.sleeping

    # --- Public state accessors ---------------------------------------

    @property
    def sim(self) -> RigidBodySimulator:
        return self._sim

    @property
    def art_w(self) -> int:
        return self._art_w

    @property
    def art_h(self) -> int:
        return self._art_h

    @property
    def cell_w(self) -> int:
        return self._cell_w

    @property
    def line_h(self) -> int:
        return self._line_h

    @property
    def frame_lines(self) -> list[str]:
        return self._frame_lines

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def paint_target_ts(self) -> float | None:
        return self._paint_target_ts

    @property
    def last_step_ts(self) -> float:
        return self._last_step_ts

    @property
    def theta_prev(self) -> float:
        return self._theta_prev

    @property
    def pos_prev(self) -> tuple[float, float]:
        return self._pos_prev

    @property
    def right_click_handler(self) -> Callable[[QPoint], None] | None:
        return self._on_right_click

    def set_right_click_handler(
        self, handler: Callable[[QPoint], None] | None,
    ) -> None:
        self._on_right_click = handler

    def is_dragging(self) -> bool:
        return self._drag_active

    # --- Frame / zoom -------------------------------------------------

    def set_frame(self, lines: list[str]) -> None:
        self._frame_lines = list(lines)
        self._measure_cells()
        self.position_changed.emit()

    def set_zoom(self, factor: float) -> None:
        if factor <= 0.0 or factor == self._zoom:
            return
        self._zoom = factor
        self._font = scale_font(self._base_font, factor)
        self._measure_cells()
        self._sim.set_config(self._zoomed_physics_config())
        self.position_changed.emit()

    def _zoomed_physics_config(self) -> RigidBodyConfig:
        """Apply the current zoom to length / force-magnitude fields of
        ``_base_physics``. Frequencies, damping ratios, and rotational
        quantities are scale-free and pass through.

        Gravity is left untouched — drag-zoom can't land exactly on 1.0,
        so scaling g would leave gravity off-base whenever the buddy is
        visually "back to normal." Upright bias still scales as z² to
        match inertia growth (I ∝ R²) so the righting time-constant is
        invariant under zoom."""
        base = self._base_physics
        z = self._zoom
        return dataclasses.replace(
            base,
            inertia=self._compute_inertia(base.mass),
            max_linear_speed=base.max_linear_speed * z,
            upright_bias_strength=base.upright_bias_strength * z * z,
            upright_bias_radius=base.upright_bias_radius * z,
            settle_speed=base.settle_speed * z,
            settle_distance=base.settle_distance * z,
        )

    # --- Geometry -----------------------------------------------------

    def _measure_cells(self) -> None:
        fm = QFontMetrics(self._font)
        self._cell_w = max(measure_block_paint_width(self._font) - 1, 1)
        self._line_h = fm.height()
        cols = max(
            (len(stripped_text(line)) for line in self._frame_lines),
            default=0,
        )
        self._cols = cols
        self._art_w = max(cols * self._cell_w, 1)
        self._art_h = max(self._line_h * len(self._frame_lines), 1)
        self._art_pixmap = None

    def com_art(self) -> tuple[float, float]:
        """Center of mass in art-frame coords. Head-heavy (see
        ``COM_Y_FRACTION``), not geometric center."""
        return (self._art_w / 2.0, self._art_h * COM_Y_FRACTION)

    def _compute_inertia(self, mass: float) -> float:
        com_x, com_y = self.com_art()
        r = math.hypot(
            max(com_x, self._art_w - com_x),
            max(com_y, self._art_h - com_y),
        )
        return max(mass * r * r * 0.5, 1.0)

    def lerped_state(self) -> tuple[float, float, float]:
        """Lerped ``(theta, com_x_world, com_y_world)`` between the two
        most recent physics snapshots. Alpha is progress past the latest
        step in units of ``FIXED_DT``, measured against the per-pump
        paint clock when active so every accessor within one pump
        returns the same value, falling back to live ``time.monotonic``
        between pumps. Theta extrapolates past α=1 at constant slope;
        position alpha is clamped to [0, 1] so the residual stays
        bounded by the adapter's AABB slack. Shortest-arc delta avoids
        a one-frame ghost flash through upright when θ crosses ±π."""
        sample_ts = (
            self._paint_target_ts
            if self._paint_target_ts is not None
            else time.monotonic()
        )
        delta_s = max(0.0, sample_ts - self._last_step_ts)
        alpha = delta_s / FIXED_DT_S
        delta_theta = self._sim.theta - self._theta_prev
        if delta_theta > math.pi:
            delta_theta -= 2.0 * math.pi
        elif delta_theta < -math.pi:
            delta_theta += 2.0 * math.pi
        theta = self._theta_prev + delta_theta * alpha
        pos_alpha = min(1.0, max(0.0, alpha))
        px, py = self._sim.position
        ppx, ppy = self._pos_prev
        return (
            theta,
            ppx + (px - ppx) * pos_alpha,
            ppy + (py - ppy) * pos_alpha,
        )

    def lerped_state_clamped(self) -> tuple[float, float, float]:
        """Like ``lerped_state`` but clamps theta alpha to [0, 1].

        QtQuick paints can land at alpha up to ~2 (vsync gap exceeds
        FIXED_DT), where the unclamped theta extrapolation visibly
        back-steps. The QWidget path runs the unclamped form because
        its paint cadence stays inside FIXED_DT."""
        sample_ts = (
            self._paint_target_ts
            if self._paint_target_ts is not None
            else time.monotonic()
        )
        delta_s = max(0.0, sample_ts - self._last_step_ts)
        alpha = max(0.0, min(1.0, delta_s / FIXED_DT_S))
        delta_theta = self._sim.theta - self._theta_prev
        if delta_theta > math.pi:
            delta_theta -= 2.0 * math.pi
        elif delta_theta < -math.pi:
            delta_theta += 2.0 * math.pi
        theta = self._theta_prev + delta_theta * alpha
        px, py = self._sim.position
        ppx, ppy = self._pos_prev
        return (theta, ppx + (px - ppx) * alpha, ppy + (py - ppy) * alpha)

    def head_world_position(self) -> QPointF:
        return self.art_frame_point_world(self._art_w / 2.0, 0.0)

    def foot_world_position(self) -> QPointF:
        return self.art_frame_point_world(self._art_w / 2.0, float(self._art_h))

    def body_angle(self) -> float:
        return self.lerped_state()[0]

    def art_frame_point_world(self, ax: float, ay: float) -> QPointF:
        """Map an art-frame coord to its world position under the
        current lerped body rotation. ``COM_world + R(theta) ·
        (art - com_art)`` — purely a function of physics state, no
        widget pos."""
        theta, cx, cy = self.lerped_state()
        com_x, com_y = self.com_art()
        dx = ax - com_x
        dy = ay - com_y
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        return QPointF(
            cx + cos_t * dx - sin_t * dy,
            cy + sin_t * dx + cos_t * dy,
        )

    def world_to_art(self, world_point: QPointF) -> QPointF | None:
        theta, cx, cy = self.lerped_state()
        com_x, com_y = self.com_art()
        dx = world_point.x() - cx
        dy = world_point.y() - cy
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        # Inverse rotation.
        ax = cos_t * dx + sin_t * dy + com_x
        ay = -sin_t * dx + cos_t * dy + com_y
        return QPointF(ax, ay)

    def art_bounds(self) -> QRect:
        return QRect(0, 0, self._art_w, self._art_h)

    def buddy_occlusion_rect_world(self) -> QRect:
        """Axis-aligned screen-space AABB of the rotated art."""
        corners = (
            self.art_frame_point_world(0.0, 0.0),
            self.art_frame_point_world(float(self._art_w), 0.0),
            self.art_frame_point_world(0.0, float(self._art_h)),
            self.art_frame_point_world(
                float(self._art_w), float(self._art_h),
            ),
        )
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        x = int(math.floor(min(xs))) - 1
        y = int(math.floor(min(ys))) - 1
        x2 = int(math.ceil(max(xs))) + 1
        y2 = int(math.ceil(max(ys))) + 1
        return QRect(x, y, max(x2 - x, 1), max(y2 - y, 1))

    def is_painted_cell_at(self, ax: float, ay: float) -> bool:
        """Hit-test an art-frame point against the actually-painted
        glyphs (not just the AABB). Mirrors ``render_art_pixmap``'s
        per-line horizontal centering."""
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

    def needs_rotated_followers(self) -> bool:
        return (
            abs(self._sim.theta) > _FOLLOWER_ROTATION_EPS
            or abs(self._sim.omega) > _FOLLOWER_OMEGA_EPS
        )

    # --- Tick ---------------------------------------------------------

    def _on_tick(self) -> None:
        body_start = time.perf_counter() if _TICK_PROFILE else 0.0
        now = time.monotonic()
        frame_time = min(now - self._last_tick_ts, _MAX_FRAME_TIME_S)
        self._last_tick_ts = now
        # Fix-Your-Timestep accumulator.
        self._accumulator += frame_time
        while self._accumulator >= FIXED_DT_S:
            self._theta_prev = self._sim.theta
            self._pos_prev = self._sim.position
            self._sim.tick(FIXED_DT_S)
            self._accumulator -= FIXED_DT_S
            self._last_step_ts = now
        primary = QGuiApplication.primaryScreen()
        refresh_hz = float(primary.refreshRate()) if primary else 60.0
        self._paint_target_ts = now + 1.0 / max(refresh_hz, 1.0)
        self.tick_offscreen_rescue(now)
        rescue_pending = (
            self._offscreen_since is not None or self._rescue_t0 is not None
        )
        is_idle = (
            self._sim.sleeping
            and not self._drag_active
            and not rescue_pending
        )
        # Skip the emit (and the four-slot cascade it drives) while
        # the buddy is fully at rest — but still emit on the
        # transition-into-rest tick so observers paint the settled
        # pose once.
        if not (is_idle and self._was_idle):
            self.position_changed.emit()
        if is_idle != self._was_idle:
            self._was_idle = is_idle
            self.awake_changed.emit(not is_idle)
        if _PHYSICS_DEBUG:
            self._debug_tick_counter += 1
            if self._debug_tick_counter % _PHYSICS_DEBUG_LOG_EVERY == 0:
                self._log_physics_debug()
        if _TICK_PROFILE:
            self._record_tick_profile(now, time.perf_counter() - body_start)
        if is_idle:
            self.sleep_tick_timer()

    def wake_tick_timer(self) -> None:
        # Fire the transition signal first so backends (Quick path's
        # frameSwapped consumer) can restart their render loops before
        # the next physics tick lands.
        if self._was_idle:
            self._was_idle = False
            self.awake_changed.emit(True)
        if not self._timer.isActive():
            now = time.monotonic()
            self._last_tick_ts = now
            # Reset interpolation state on wake; otherwise _last_step_ts
            # is stale and the next paint's α explodes.
            self._accumulator = 0.0
            self._theta_prev = self._sim.theta
            self._pos_prev = self._sim.position
            self._last_step_ts = now
            self._timer.start()

    def sleep_tick_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def is_tick_active(self) -> bool:
        return self._timer.isActive()

    def is_idle(self) -> bool:
        return self._was_idle

    def _record_tick_profile(self, now: float, body_s: float) -> None:
        prev = self._tick_profile_last_perf
        self._tick_profile_last_perf = now
        if prev > 0.0:
            interval = now - prev
            if interval < 0.5:
                self._tick_intervals.append(interval)
        self._tick_durations.append(body_s)
        if now - self._tick_profile_last_log < 1.0:
            return
        self._tick_profile_last_log = now
        if not self._tick_intervals:
            return
        intervals = sorted(self._tick_intervals)
        durations = sorted(self._tick_durations)

        def pct(xs: list[float], p: float) -> float:
            return xs[min(len(xs) - 1, int(len(xs) * p))] * 1000.0

        total = sum(intervals)
        fps = len(intervals) / total if total > 0 else 0.0
        log.info(
            "tick: %.0ffps n=%d  interval p50/p95/p99/max=%.1f/%.1f/%.1f/%.1fms  "
            "body p50/p95/p99/max=%.2f/%.2f/%.2f/%.2fms",
            fps, len(intervals),
            pct(intervals, 0.50), pct(intervals, 0.95),
            pct(intervals, 0.99), intervals[-1] * 1000.0,
            pct(durations, 0.50), pct(durations, 0.95),
            pct(durations, 0.99), durations[-1] * 1000.0,
        )
        self._tick_intervals.clear()
        self._tick_durations.clear()

    # --- Off-screen rescue --------------------------------------------

    def screen_rects(self) -> list[Rect]:
        rects: list[Rect] = []
        for screen in QGuiApplication.screens():
            geom = screen.geometry()
            rects.append((geom.left(), geom.top(), geom.right(), geom.bottom()))
        return rects

    def tick_offscreen_rescue(self, now: float) -> None:
        if self._drag_active:
            self._offscreen_since = None
            self._rescue_t0 = None
            return

        if self._rescue_t0 is not None:
            elapsed = now - self._rescue_t0
            t = min(1.0, elapsed / OFFSCREEN_RESCUE_DURATION_S)
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
            self._sim.position, self.screen_rects(), OFFSCREEN_RESCUE_INSET,
        )
        if target is None:
            self._offscreen_since = None
            return
        if self._offscreen_since is None:
            self._offscreen_since = now
            return
        if now - self._offscreen_since >= OFFSCREEN_RESCUE_DELAY_S:
            self._offscreen_since = None
            self._rescue_start = self._sim.position
            self._rescue_target = target
            self._rescue_t0 = now

    def cancel_offscreen_rescue(self) -> None:
        self._offscreen_since = None
        self._rescue_t0 = None

    # --- Drag input ---------------------------------------------------

    def begin_drag(
        self, art_pos: QPointF, cursor_global: QPointF,
    ) -> None:
        """Start a grab. Convert the art-frame click point to a body-
        local offset (relative to COM) and hand it to the simulator
        as the constraint anchor."""
        self.cancel_offscreen_rescue()
        com_x, com_y = self.com_art()
        local_x = art_pos.x() - com_x
        local_y = art_pos.y() - com_y
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
        self.wake_tick_timer()

    def set_grab_target(self, x: float, y: float) -> None:
        self._sim.set_grab_target(x, y)
        self.wake_tick_timer()

    def end_drag(self) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        self._sim.end_grab()
        self.maybe_edge_dock()

    def maybe_edge_dock(self) -> None:
        """Snap the home anchor to the nearest screen edge when the
        buddy's COM is dropped close to one."""
        cx, cy = self._sim.position
        screen = QGuiApplication.screenAt(QPoint(int(cx), int(cy)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        new_x, new_y = cx, cy
        if cx - geom.left() < EDGE_DOCK_THRESHOLD:
            new_x = float(geom.left())
        elif geom.right() - cx < EDGE_DOCK_THRESHOLD:
            new_x = float(geom.right())
        if cy - geom.top() < EDGE_DOCK_THRESHOLD:
            new_y = float(geom.top())
        elif geom.bottom() - cy < EDGE_DOCK_THRESHOLD:
            new_y = float(geom.bottom())
        if (new_x, new_y) != (cx, cy):
            self._sim.snap_home(new_x, new_y)

    # --- Master sprite -----------------------------------------------

    def render_art_pixmap(self, device_pixel_ratio: float = 1.0) -> QPixmap:
        """Return the master sprite for the current frame. Cached by
        ``(lines, font_family)``; zoom is not in the key because the
        master is rasterized once at ``_MASTER_ZOOM`` and the
        downstream painter rescales via a single bilinear sample."""
        if self._art_pixmap is not None:
            return self._art_pixmap
        cache_key = (
            tuple(self._frame_lines),
            self._master_font.family(),
        )
        cached = self._pixmap_cache.get(cache_key)
        if cached is not None:
            self._art_pixmap = cached
            return cached

        cell_w = self._cell_w_master
        cols = self._cols
        rows = len(self._frame_lines)
        line_h = self._line_h_master
        ascent = max(QFontMetrics(self._master_font).ascent(), 1)
        y_stretch = line_h / ascent

        scale = max(device_pixel_ratio * _MASTER_SUPERSAMPLE, 1.0)
        phys_w = max(int(math.ceil(cols * cell_w * scale)), 1)
        phys_h = max(int(math.ceil(rows * line_h * scale)), 1)

        image = QImage(
            phys_w, phys_h, QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.scale(scale, scale)
        painter.setFont(self._master_font)

        total_w = cols * cell_w
        y_top = 0
        for line in self._frame_lines:
            chars = len(stripped_text(line))
            base_x = (total_w - chars * cell_w) // 2
            col = 0
            for seg in parse_markup(line):
                color = QColor(seg.color) if seg.color else _FG_COLOR
                painter.setPen(color)
                for ch in seg.text:
                    x = base_x + col * cell_w
                    rect = QRect(x, y_top, cell_w + 1, line_h)
                    if not paint_block_char(painter, ch, rect, color):
                        painter.save()
                        painter.translate(x, y_top)
                        painter.scale(1.0, y_stretch)
                        painter.drawText(0, ascent, ch)
                        painter.restore()
                    col += 1
            y_top += line_h
        painter.end()

        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(device_pixel_ratio * _MASTER_SUPERSAMPLE)
        self._pixmap_cache[cache_key] = pixmap
        self._art_pixmap = pixmap
        return pixmap

    # --- Physics debug -------------------------------------------------

    def paint_physics_debug(
        self, painter: QPainter, com_widget: tuple[int, int],
    ) -> None:
        """Render the magenta-crosshair physics HUD into ``painter`` at
        widget-local coords. Public so the QWidget adapter can call it
        from its paintEvent without reaching for private state."""
        sim = self._sim
        cwx, cwy = com_widget
        painter.setPen(QColor("magenta"))
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

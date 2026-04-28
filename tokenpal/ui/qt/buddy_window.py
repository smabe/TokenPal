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
import logging
import math
import os
import sys
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QRegion,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.palette import BUDDY_GREEN
from tokenpal.ui.qt import _paint_trace
from tokenpal.ui.qt._screen_bounds import Rect, offscreen_rescue_target
from tokenpal.ui.qt._text_fx import paint_block_char, scale_font
from tokenpal.ui.qt.markup import parse_markup, stripped_text
from tokenpal.ui.qt.physics import RigidBodyConfig, RigidBodySimulator

log = logging.getLogger(__name__)

# Pump rate for the accumulator loop. Qt's PreciseTimer floor on
# Windows 11 is ~6 ms regardless of setInterval (multimedia-timer
# dispatch jitter), so asking for less is wasted.
_TICK_INTERVAL_MS = 6
# Fixed physics step. Decoupled from the pump rate: each pump call
# accumulates wall-clock time and runs as many ``FIXED_DT`` integrations
# as needed to drain the accumulator. Matches Glenn Fiedler's "Fix Your
# Timestep" — physics state advances by an invariant dt every step,
# eliminating dt-jitter as a source of integration noise. 240 Hz is
# above every common display refresh so the accumulator drains in
# 1-2 steps per pump regardless of vsync rate.
_FIXED_DT_S = 1.0 / 240.0
# Cap accumulated time to avoid the death-spiral failure mode (a long
# stall building up many seconds of physics to catch up on, which then
# blows the next pump's budget and stalls again).
_MAX_FRAME_TIME_S = 0.25
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

# Pixel offsets between the buddy and his attached followers. The
# overlay applies these in screen-axis coords on the QWidget path
# (rotated through `_body_aligned_offset`); the Quick path embeds
# them directly into the pivot-local position so the pivot's
# rotation supplies the body-aligned twist for free.
BUBBLE_HOVER_OFFSET_Y = 16
DOCK_OFFSET_Y = 4

# Reference zoom factor at which the invariant master sprite is
# rasterized. paintEvent's drawPixmap implicitly scales to the current
# zoom, so the master stays crisp from 0.5× up to roughly this value;
# beyond it bilinear sampling starts to read soft. Per-pose memory grows
# as MASTER_ZOOM² × supersample² — see plans/master-sprite.md.
_MASTER_ZOOM = 2.0
# Supersample factor applied on top of MASTER_ZOOM. Pushes the rotation
# source resolution well above the paint surface so bilinear sampling
# during a rotated blit reads clean.
_MASTER_SUPERSAMPLE = 2

# Enable an on-screen HUD + file log of body state by setting
# TOKENPAL_PHYSICS_DEBUG=1 in the environment before launch.
_PHYSICS_DEBUG = bool(os.environ.get("TOKENPAL_PHYSICS_DEBUG"))
_PHYSICS_DEBUG_LOG_EVERY = 3
_PHYSICS_DEBUG_LOG_PATH = "/tmp/tokenpal-physics.log"

# TOKENPAL_TICK_PROFILE=1 logs Qt-main-thread tick latency at 1 Hz so we
# can see whether stutter is the timer slipping (GIL contention) or the
# tick body itself getting heavy. Interval = wall-clock between two
# consecutive _on_tick fires; body = how long _on_tick spent. If interval
# p95 >> 16.7ms while body p95 stays small, the main thread is starving
# for the GIL.
_TICK_PROFILE = bool(os.environ.get("TOKENPAL_TICK_PROFILE"))

_FG_COLOR = QColor(BUDDY_GREEN)


_BLOCK_PAINT_WIDTH_CACHE: dict[tuple[str, int], int] = {}


def _measure_block_paint_width(font: QFont) -> int:
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
    # resized the window. Followers (bubble, dock, resize grip) use
    # this to track the buddy across the screen.
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
        # Master sprite font + cell metrics. Fixed at _MASTER_ZOOM and
        # never touched after init, so the per-pose pixmap cache is
        # zoom-invariant: zooming changes the destination rect of
        # drawPixmap, not the source bitmap. Render path is one
        # bilinear sample of the master through the world transform —
        # sprite-quad style.
        self._master_font = scale_font(self._base_font, _MASTER_ZOOM)
        self._cell_w_master = max(
            _measure_block_paint_width(self._master_font) - 1, 1,
        )
        self._line_h_master = QFontMetrics(self._master_font).height()

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
        # Cached static sprite for the current frame. Rendered once per
        # (frame_lines, font) tuple at supersampled resolution;
        # paintEvent blits it through a rotation transform — same model
        # as a game sprite. The 200-glyph paint loop we used to do
        # every frame re-hinted text per sub-pixel position and
        # produced visible per-frame aliasing variance ("breaking up at
        # the edges"). Bitmap-blit has no such per-frame variance:
        # source is invariant, only sampling moves.
        self._art_pixmap: QPixmap | None = None
        # Sprite atlas keyed by (lines, font_family). Master is
        # rasterized once at _MASTER_ZOOM × _MASTER_SUPERSAMPLE; zoom
        # falls out via drawPixmap's destination rect, so a single
        # entry per pose serves every zoom level.
        self._pixmap_cache: dict[tuple[object, ...], QPixmap] = {}
        # Where the COM sits inside the widget after the rotated-AABB
        # padding. Set by _recompute_geometry; equals the COM's
        # screen-space offset from the widget's top-left.
        self._com_widget: tuple[int, int] = (0, 0)
        self._last_mask_rect: QRect = QRect()

        self._measure_cells()

        self._base_physics = physics_config or RigidBodyConfig()
        self._sim = RigidBodySimulator(
            home=initial_anchor, config=self._zoomed_physics_config(),
        )

        # Fix-Your-Timestep state must be initialized before the first
        # _recompute_geometry call (it reads _theta_prev for AABB
        # slack). ``_build_transform`` lerps between (_theta_prev,
        # sim.theta) using α = (now − _last_step_ts) / _FIXED_DT_S.
        # _pos_prev mirrors _theta_prev so paint can lerp position the
        # same way — without it, position is up to one physics step
        # stale at paint time and the staleness scales with screen-px
        # velocity, which scales with zoom.
        now = time.monotonic()
        self._accumulator = 0.0
        self._theta_prev = self._sim.theta
        self._pos_prev = self._sim.position
        self._last_step_ts = now

        # _recompute_geometry reads sim.theta, so the sim must exist first.
        self._recompute_geometry()
        self._move_to_com()

        self._drag_active = False

        self._offscreen_since: float | None = None
        self._rescue_t0: float | None = None
        self._rescue_start: tuple[float, float] = (0.0, 0.0)
        self._rescue_target: tuple[float, float] = (0.0, 0.0)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._last_tick_ts = now
        # Start settled — the simulator wakes on grab / impulse.

        self._debug_tick_counter = 0
        self._debug_log_fp = None
        if _PHYSICS_DEBUG:
            self._debug_log_fp = open(  # noqa: SIM115 — lifetime = widget
                _PHYSICS_DEBUG_LOG_PATH, "w", buffering=1,
            )

        self._tick_intervals: deque[float] = deque(maxlen=600)
        self._tick_durations: deque[float] = deque(maxlen=600)
        self._tick_profile_last_log = 0.0
        self._tick_profile_last_perf = 0.0

        # Shared paint clock. Frozen at the start of each pump and read
        # by every accessor that lerps (body_angle, head_world_position,
        # _build_transform via _lerped_state). Without this, the buddy's
        # paintEvent calls time.monotonic() at one microsecond and each
        # signal-slot follower (bubble, dock_mock, grip) calls it at
        # progressively later microseconds, so they all end up painting
        # different lerped angles within the same pump — the visible
        # ghost rotating with the buddy. Freezing target = now + 1
        # refresh period predicts where the body will be at the next
        # DWM composite; aiming the lerp at the screen's photon time
        # rather than the paint sample time is the standard frame-pacing
        # contract (Apple WWDC23 §10075, NVIDIA Reflex, Handmade Hero).
        self._paint_target_ts: float | None = None

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

    def set_zoom(self, factor: float) -> None:
        """Rescale the buddy by ``factor`` (1.0 = original size).
        Recomputes the physics config so gravity, inertia, upright
        bias, and settle thresholds all track the new size — otherwise
        a 2× buddy swings ~√2× slower than 1× and rights itself half
        as snappily because the force-magnitude params stay constant
        while inertia (which scales as size²) doesn't."""
        if factor <= 0.0 or factor == self._zoom:
            return
        self._zoom = factor
        self._font = scale_font(self._base_font, factor)
        self._measure_cells()
        self._sim.set_config(self._zoomed_physics_config())
        self._refresh_view()

    def _zoomed_physics_config(self) -> RigidBodyConfig:
        """Apply the current zoom to length / force-magnitude fields of
        ``_base_physics``. Frequencies, damping ratios, and rotational
        quantities (rad/s, rad) are scale-free and pass through.

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
        self._art_pixmap = None

    def _font_init_metrics(self) -> None:
        """Recompute ``_cell_w`` and ``_line_h`` from the current font.

        Treat the art as a fixed-pitch grid rather than trusting
        ``horizontalAdvance`` on the whole line. Menlo's reported advance
        for block glyphs (U+2588 and friends) includes side bearings the
        painter doesn't actually draw — stepping exactly at the reported
        advance leaves 1-pixel seams between blocks. We step by the
        glyph's painted width minus one so neighbours merge into a solid
        field. Row step uses ``height()`` so the cell aspect matches the
        terminal cell aspect Textual renders against — block fills extend
        full ``line_h`` so adjacent rows still touch with zero gap.
        """
        fm = QFontMetrics(self._font)
        self._cell_w = max(_measure_block_paint_width(self._font) - 1, 1)
        self._line_h = fm.height()

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
        body angle (plus a slack for paint-time θ extrapolation), then
        place ``_com_widget`` so the body's COM lands at the right spot
        inside the widget.

        ``_build_transform`` paints with α-lerp between ``_theta_prev``
        and ``sim.theta``, where α grows continuously between pumps and
        can exceed 1 if the pump interval slips past ``_FIXED_DT_S``.
        AABB must cover the full range of paintable θ to keep the
        input mask from clipping silhouettes. Slack to α=2 (covers up
        to one full extra step of forward extrapolation)."""
        com_x, com_y = self._com_art()
        # Shortest-arc delta — same wraparound fix as _build_transform.
        # Without it, AABB endpoints during a θ=±π crossing point at
        # nonsense angles and the widget resizes wrong for one tick.
        delta_theta = self._sim.theta - self._theta_prev
        if delta_theta > math.pi:
            delta_theta -= 2.0 * math.pi
        elif delta_theta < -math.pi:
            delta_theta += 2.0 * math.pi
        angles = (
            self._theta_prev,
            self._sim.theta + delta_theta,  # α=2 forward extrapolation
        )
        corners: list[QPointF] = []
        for angle in angles:
            rot = QTransform()
            rot.translate(com_x, com_y)
            rot.rotate(math.degrees(angle))
            rot.translate(-com_x, -com_y)
            corners.append(rot.map(QPointF(0.0, 0.0)))
            corners.append(rot.map(QPointF(float(self._art_w), 0.0)))
            corners.append(rot.map(QPointF(0.0, float(self._art_h))))
            corners.append(rot.map(
                QPointF(float(self._art_w), float(self._art_h)),
            ))
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        # 1 px slack for AA glyph edges + extra slack for the
        # position-lerp residual baked into _build_transform: paint may
        # shift the bitmap by up to one physics step's worth of motion
        # (|v| × FIXED_DT) plus 1 px integer quantization. Without the
        # extra slack, fast motion clips the bitmap at the AABB edge.
        vx, vy = self._sim.velocity
        pos_slack = int(math.ceil(max(abs(vx), abs(vy)) * _FIXED_DT_S)) + 1
        min_x = int(math.floor(min(xs))) - 1 - pos_slack
        min_y = int(math.floor(min(ys))) - 1 - pos_slack
        max_x = int(math.ceil(max(xs))) + 1 + pos_slack
        max_y = int(math.ceil(max(ys))) + 1 + pos_slack
        width = max(max_x - min_x, 1)
        height = max(max_y - min_y, 1)
        self._com_widget = (int(com_x) - min_x, int(com_y) - min_y)
        # Skip the WM resize when nothing changed. resize() is called
        # every tick from _refresh_view; on Windows even a no-op resize
        # can dispatch WM_SIZE handling that costs ~1 ms — enough to
        # push the tick interval past vsync.
        if self.size().width() != width or self.size().height() != height:
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
        """Lerped rotation of the body in the world frame, in radians.
        Followers (bubble, dock_mock, grip) use this to rotate with the
        buddy so they stay rigidly attached during a swing. Must match
        what ``_build_transform`` paints — returning the raw simulator
        ``theta`` here means followers paint at the post-step value
        while the buddy paints at the lerped/extrapolated value, leaving
        the follower one step behind. At 240 Hz that ~0.005 rad gap
        reads as a ghost rotating with the buddy."""
        return self._lerped_state()[0]

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

    def _lerped_state(self) -> tuple[float, float, float]:
        """Lerped ``(theta, com_x, com_y)`` between the two most recent
        physics snapshots. Alpha is progress past the latest step in
        units of ``FIXED_DT``, measured against the per-pump paint
        clock (``_paint_target_ts``) when active so every accessor
        within one pump returns the same value, falling back to live
        ``time.monotonic()`` between pumps. Theta extrapolates past
        α=1 at constant slope (graceful pump-stall recovery); position
        alpha is clamped to [0, 1] so the residual stays bounded by
        AABB slack (see ``_recompute_geometry``). Shortest-arc delta
        avoids a one-frame ghost flash through upright when θ crosses
        ±π."""
        sample_ts = (
            self._paint_target_ts
            if self._paint_target_ts is not None
            else time.monotonic()
        )
        delta_s = max(0.0, sample_ts - self._last_step_ts)
        alpha = delta_s / _FIXED_DT_S
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

    def _build_transform(self) -> QTransform:
        """Forward art-frame → widget-frame transform: translate art
        origin to ``-com_art``, rotate by lerped θ, translate to
        ``com_widget`` plus a sub-pixel residual that absorbs
        widget.move integer quantization and the position-lerp gap.
        ``paintEvent`` applies the same transform so hit-test stays
        in sync. Painted COM lands at the continuous lerped position,
        not at ``int(sim.position)`` — the latter judders by up to one
        physics step's worth of motion per paint, scaled by zoom."""
        theta, cx_lerp, cy_lerp = self._lerped_state()
        widget_pos = self.pos()
        sub_x = cx_lerp - widget_pos.x() - self._com_widget[0]
        sub_y = cy_lerp - widget_pos.y() - self._com_widget[1]

        t = QTransform()
        t.translate(self._com_widget[0] + sub_x, self._com_widget[1] + sub_y)
        t.rotate(math.degrees(theta))
        com_x, com_y = self._com_art()
        t.translate(-com_x, -com_y)
        return t

    def _update_click_mask(self) -> None:
        """Mask the buddy to its widget rect so clicks fall through to
        whatever's behind any antialias slack. Since
        ``_recompute_geometry`` sizes the widget to the rotated-art
        AABB, the widget rect is already the tightest legal mask.

        Skip the setMask call when the widget rect hasn't changed.
        setMask hands a QRegion to the WM and on Windows the round-trip
        costs ~1 ms — at 250 Hz that's 25 % of the tick budget burned
        re-asserting the same mask."""
        rect = self.rect()
        if rect == self._last_mask_rect:
            return
        self.setMask(QRegion(rect))
        self._last_mask_rect = rect

    def _refresh_view(self) -> None:
        """Resize the widget to the rotated-art AABB, position it on
        the COM, refresh the click-through mask, and force a paint.

        ``repaint()`` (synchronous) instead of ``update()`` so every
        pump's paint lands in the same Qt event-loop iteration as the
        followers' synchronous paints (their set_pose slots ran via the
        ``position_changed`` emit inside ``_move_to_com`` above). All
        windows then reach DWM with state from the same pump and share
        a composite frame, killing the inter-window 1-pump offset that
        Qt's ``update()`` coalescing was producing for the followers."""
        self._recompute_geometry()
        self._move_to_com()
        self._update_click_mask()
        self.repaint()

    # --- Tick / timer ---------------------------------------------------

    def _on_tick(self) -> None:
        body_start = time.perf_counter() if _TICK_PROFILE else 0.0
        now = time.monotonic()
        frame_time = min(now - self._last_tick_ts, _MAX_FRAME_TIME_S)
        self._last_tick_ts = now
        # Fix-Your-Timestep accumulator. Drain in fixed-dt steps so the
        # integrator and constraint solver always see the same dt — no
        # γ/β jitter, no damping noise. Save the state *before* each
        # step so paintEvent can lerp between (prev, curr) for a smooth
        # rendered θ regardless of when the paint samples wall-clock.
        self._accumulator += frame_time
        while self._accumulator >= _FIXED_DT_S:
            self._theta_prev = self._sim.theta
            self._pos_prev = self._sim.position
            self._sim.tick(_FIXED_DT_S)
            self._accumulator -= _FIXED_DT_S
            self._last_step_ts = now
        screen = self.screen() or QGuiApplication.primaryScreen()
        refresh_hz = float(screen.refreshRate()) if screen else 60.0
        self._paint_target_ts = now + 1.0 / max(refresh_hz, 1.0)
        self._tick_offscreen_rescue(now)
        self._refresh_view()
        if _PHYSICS_DEBUG:
            self._debug_tick_counter += 1
            if self._debug_tick_counter % _PHYSICS_DEBUG_LOG_EVERY == 0:
                self._log_physics_debug()
        if _TICK_PROFILE:
            self._record_tick_profile(now, time.perf_counter() - body_start)
        rescue_pending = (
            self._offscreen_since is not None or self._rescue_t0 is not None
        )
        if self._sim.sleeping and not self._drag_active and not rescue_pending:
            self._sleep_timer()

    def _record_tick_profile(self, now: float, body_s: float) -> None:
        prev = self._tick_profile_last_perf
        self._tick_profile_last_perf = now
        if prev > 0.0:
            interval = now - prev
            # Drop wake-from-sleep gaps; sim sleeps when settled.
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

    def _wake_timer(self) -> None:
        if not self._timer.isActive():
            now = time.monotonic()
            self._last_tick_ts = now
            # Reset interpolation state on wake. Otherwise _last_step_ts
            # is stale (frozen at sleep time), the next paint's α
            # explodes (elapsed = now - very_old), and the render
            # extrapolates θ to nonsense. _theta_prev is set to current
            # so the first lerp before any new step renders the static
            # pose.
            self._accumulator = 0.0
            self._theta_prev = self._sim.theta
            self._pos_prev = self._sim.position
            self._last_step_ts = now
            self._timer.start()

    def _sleep_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    # --- Off-screen rescue ----------------------------------------------

    def _screen_rects(self) -> list[Rect]:
        # Full physical geometry, not availableGeometry — the buddy is
        # always-on-top so he's visually present in the menu-bar / dock
        # strip, and rescue should only fire when he's past the actual
        # display edge. (Edge-dock still uses availableGeometry because
        # snapping should respect the dock.)
        rects: list[Rect] = []
        for screen in QGuiApplication.screens():
            geom = screen.geometry()
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
        # Bilinear sample of the master pixmap through the world
        # transform — same op as a game engine drawing a sprite quad.
        # Master is rasterized at _MASTER_ZOOM × _MASTER_SUPERSAMPLE so
        # zoom + rotation share one resampling pass with plenty of
        # source resolution.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setWorldTransform(self._build_transform())
        painter.drawPixmap(
            QRect(0, 0, self._art_w, self._art_h),
            self._render_art_pixmap(),
        )
        if _paint_trace.enabled():
            theta, cx, cy = self._lerped_state()
            _paint_trace.log_paint("buddy", theta=theta, pos=(cx, cy))

        if _PHYSICS_DEBUG:
            # Debug HUD paints on top of the rotated art but in widget-
            # local coords, so the text stays upright regardless of θ.
            painter.setWorldTransform(QTransform())
            self._paint_physics_debug(painter)

    def _render_art_pixmap(self) -> QPixmap:
        """Return the master sprite for the current frame. Looks up the
        atlas by ``(lines, font_family)`` — zoom is not in the key
        because the master is rasterized once at ``_MASTER_ZOOM`` and
        ``paintEvent``'s ``drawPixmap`` rescales to the active zoom via
        a single bilinear sample.

        Each cell is ``_line_h_master`` tall (matching block-fill
        geometry) and glyphs are stretched vertically via
        ``painter.scale(1, line_h/ascent)`` so drawText output fills
        the same rect as ``paint_block_char`` — no empty strip on
        Windows where Consolas's height/ascent ratio is larger than
        Menlo's."""
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

        dpr = self.devicePixelRatioF()
        scale = max(dpr * _MASTER_SUPERSAMPLE, 1.0)
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
        pixmap.setDevicePixelRatio(dpr * _MASTER_SUPERSAMPLE)
        self._pixmap_cache[cache_key] = pixmap
        self._art_pixmap = pixmap
        return pixmap

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

"""Frameless, transparent, always-on-top buddy window.

The buddy hangs from a pivot point that tracks the cursor during a drag.
When the user grabs him anywhere on the art, that grab point becomes the
pendulum pivot — the rest of the body rotates around it. Grab by the
head → normal dangle. Grab by the foot → dangles upside-down from the
foot. Release → residual angular momentum swings him a few times before
damping out.

Physics lives in ``tokenpal.ui.qt.physics.PendulumSimulator``; this
module owns the Qt plumbing (drag input, 60 Hz tick, paint-time
rotation, widget sizing for the rotated art).
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.palette import BUDDY_GREEN
from tokenpal.ui.qt.markup import parse_markup, stripped_text
from tokenpal.ui.qt.physics import PendulumConfig, PendulumSimulator

_PHYSICS_HZ = 60
_TICK_MS = int(1000 / _PHYSICS_HZ)
_FLING_SAMPLE_WINDOW_S = 0.08  # how much recent cursor motion counts as fling
_FLING_SAMPLE_MAX = 32          # ring-buffer cap
_EDGE_DOCK_THRESHOLD = 20       # px from screen edge triggers snap
_ART_MARGIN = 6                 # breathing room around the unrotated art
# Head-heavy center of mass. In art-frame y-fraction: 0.0 is the crown,
# 1.0 is the feet. 0.30 puts the COM in the chest/upper-torso region —
# so grabs at the geometric center still have a lever arm (the body
# preferentially rotates head-down) and foot-grabs dangle upside-down
# with the head as the heaviest falling point. Adjust together with
# PendulumConfig.mass to tune the overall swing feel.
_COM_Y_FRACTION = 0.30
# Nudge distance applied to new_theta in _begin_drag when it lands near
# the inverted-pendulum unstable equilibrium ±π. 0.015 rad ≈ 0.86°,
# below any visually noticeable snap.
_UNSTABLE_EPS = 0.015

# Enable an on-screen HUD + file log of pivot/theta/velocity by setting
# TOKENPAL_PHYSICS_DEBUG=1 in the environment before launch. Stays off
# by default so normal use has no visible overlay.
_PHYSICS_DEBUG = bool(os.environ.get("TOKENPAL_PHYSICS_DEBUG"))
# Log cadence in ticks; 3 = 20 Hz at a 60 Hz physics loop.
_PHYSICS_DEBUG_LOG_EVERY = 3
# Dedicated physics trace file, separate from tokenpal.log so it's
# trivial to grep and won't clobber the app log at 20 Hz.
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

    During a drag the cursor IS the pivot — wherever you grabbed him on
    the art is the anchor point. Release leaves any residual angular
    velocity in the pendulum, so a fast whip sends him swinging before
    he settles back to hanging straight down.
    """

    # Fires whenever the body physically moves — either because the
    # physics tick advanced the simulator or because ``set_frame``
    # resized the window. Consumers (the speech bubble) use this to
    # follow the buddy across the screen and track the rotating head.
    position_changed = Signal()


    def __init__(
        self,
        frame_lines: list[str],
        initial_anchor: tuple[float, float] = (400.0, 200.0),
        physics_config: PendulumConfig | None = None,
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

        # --- Art/pivot geometry. See _recompute_geometry for the model. ---
        self._cols = 0
        self._cell_w = 1
        self._line_h = 1
        self._art_w = 1
        self._art_h = 1
        # Pivot in "art frame" coords (art top-left = (0, 0)). Starts at
        # top-center so the default hang matches the pre-swing-it look.
        self._pivot_art: tuple[float, float] = (0.0, 0.0)
        # Set inside _recompute_geometry.
        self._art_offset: tuple[int, int] = (0, 0)
        self._pivot_widget: tuple[int, int] = (0, 0)

        self._measure_cells()
        # Default pivot = art top-center (between the antennae).
        self._pivot_art = (self._art_w / 2.0, 0.0)
        self._recompute_geometry()

        length = self._pendulum_length(self._pivot_art)
        self._sim = PendulumSimulator(
            pivot=initial_anchor,
            length=length,
            config=physics_config,
        )
        self._move_to_pivot()

        self._drag_active = False
        self._fling_samples: deque[tuple[float, QPointF]] = deque(
            maxlen=_FLING_SAMPLE_MAX,
        )

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._last_tick_ts = time.monotonic()
        # Start settled — the pendulum wakes on drag or impulse.

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

    def set_frame(self, lines: list[str]) -> None:
        self._frame_lines = list(lines)
        # Reset pivot to top-center when the art changes underfoot — old
        # pivot_art coords may no longer land on a visible pixel.
        self._measure_cells()
        self._pivot_art = (self._art_w / 2.0, 0.0)
        self._recompute_geometry()
        self._sim.set_length(self._pendulum_length(self._pivot_art))
        self._move_to_pivot()
        self.update()

    # --- Geometry --------------------------------------------------------

    def _measure_cells(self) -> None:
        """Compute per-cell dimensions and art bounding box from the font
        and frame lines. Updates ``_cell_w``, ``_line_h``, ``_cols``,
        ``_art_w``, ``_art_h``."""
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
        ``_COM_Y_FRACTION``), not geometric center — that way a grab at
        the waist still has a lever arm and the body picks a preferred
        orientation instead of spinning indifferently."""
        return (self._art_w / 2.0, self._art_h * _COM_Y_FRACTION)

    def _pendulum_length(self, pivot_art: tuple[float, float]) -> float:
        """Pendulum length = distance from pivot to the buddy's (head-
        heavy) center of mass."""
        com_x, com_y = self._com_art()
        return math.hypot(com_x - pivot_art[0], com_y - pivot_art[1])

    def _angle_of_com_offset(self) -> float:
        """Angle from +y (down in screen coords) to the pivot→COM vector,
        CW. Baked into the paint rotation so ``physics.theta = 0`` always
        hangs the COM directly below the pivot, regardless of where on
        the art the user grabbed."""
        com_x, com_y = self._com_art()
        dx = com_x - self._pivot_art[0]
        dy = com_y - self._pivot_art[1]
        # atan2(dx, dy) gives the CW-from-+y angle: (0,1) → 0,
        # (0,-1) → π, (1,0) → π/2, (-1,0) → -π/2. See the plan comment.
        return math.atan2(dx, dy)

    def _recompute_geometry(self) -> None:
        """Size the widget to fit the art rotated any amount around the
        current pivot, and cache the art-draw offset + widget-local pivot
        coord. Called on init, on ``set_frame``, and on every press when
        the pivot changes."""
        px, py = self._pivot_art
        # Furthest art corner from pivot — rotation around pivot reaches
        # no further than this, so padding by this amount on all sides
        # guarantees the rotated art never clips.
        corners = (
            (0.0, 0.0),
            (float(self._art_w), 0.0),
            (0.0, float(self._art_h)),
            (float(self._art_w), float(self._art_h)),
        )
        radius = max(
            math.hypot(cx - px, cy - py) for cx, cy in corners
        )
        radius = max(radius, 1.0) + _ART_MARGIN
        size = int(math.ceil(2 * radius))
        pivot_widget_x = int(radius)
        pivot_widget_y = int(radius)
        self._art_offset = (
            pivot_widget_x - int(px),
            pivot_widget_y - int(py),
        )
        self._pivot_widget = (pivot_widget_x, pivot_widget_y)
        self.resize(size, size)

    def _move_to_pivot(self) -> None:
        """Position the top-left so ``_pivot_widget`` lands on the
        simulator's pivot (in global screen coords). Emits
        ``position_changed`` only when the integer widget position
        actually changes — downstream followers (bubble, dock) rebuild
        a transform on each emission, so firing every tick during a
        quiescent pivot is wasted work."""
        px, py = self._sim.pivot
        new_x = int(px) - self._pivot_widget[0]
        new_y = int(py) - self._pivot_widget[1]
        old_pos = self.pos()
        if new_x == old_pos.x() and new_y == old_pos.y():
            # Still need to notify the bubble when *rotation* changes
            # at a fixed pivot (body swinging but window not moving),
            # because head_world_position drifts with θ even if the
            # widget rect doesn't. Emit unconditionally while the
            # sim is active; skip only when it's asleep.
            if self._sim.sleeping:
                return
            self.position_changed.emit()
            return
        self.move(new_x, new_y)
        self.position_changed.emit()

    def head_world_position(self) -> QPointF:
        """Rotated head-anchor (top-center of the original art) in
        global screen coords. Speech bubble follows this so it stays
        attached through a swing, even when the buddy is upside-down."""
        return self._art_point_world(self._art_w / 2.0, 0.0)

    def foot_world_position(self) -> QPointF:
        """Rotated foot-anchor (bottom-center of the original art) in
        global screen coords. The chat dock hangs under this."""
        return self._art_point_world(self._art_w / 2.0, float(self._art_h))

    def _art_point_world(self, ax: float, ay: float) -> QPointF:
        widget_pos = self.pos()
        p_widget = self._build_transform().map(QPointF(ax, ay))
        return QPointF(
            widget_pos.x() + p_widget.x(),
            widget_pos.y() + p_widget.y(),
        )

    def _build_transform(self) -> QTransform:
        """Forward art-frame → widget-frame transform. Apply the same
        ops in ``paintEvent`` via ``painter.translate/rotate`` so the
        hit-test stays in sync with what's drawn."""
        t = QTransform()
        t.translate(self._pivot_widget[0], self._pivot_widget[1])
        angle_deg = math.degrees(
            self._sim.theta + self._angle_of_com_offset(),
        )
        t.rotate(angle_deg)
        t.translate(-self._pivot_art[0], -self._pivot_art[1])
        return t

    # --- Tick / timer ---------------------------------------------------

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick_ts
        self._last_tick_ts = now
        self._sim.tick(min(dt, 1.0 / 30.0))  # clamp dt to survive stalls
        self._move_to_pivot()
        # Rotation changed → repaint even if the widget didn't move.
        self.update()
        if _PHYSICS_DEBUG:
            self._debug_tick_counter += 1
            if self._debug_tick_counter % _PHYSICS_DEBUG_LOG_EVERY == 0:
                self._log_physics_debug()
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

        # Identify the art pixel under the cursor. If the click landed
        # in transparent padding (widget is 2–3× the art), ignore it so
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
        """Switch the pivot to the grabbed art pixel while preserving
        the buddy's current visual pose. The new pivot in world coords
        is the cursor position."""
        self._reconfigure_pivot(
            new_pivot_art=(art_pos.x(), art_pos.y()),
            new_pivot_world=(cursor_global.x(), cursor_global.y()),
        )
        self._drag_active = True
        self._fling_samples.clear()
        self._fling_samples.append((time.monotonic(), cursor_global))
        self._wake_timer()

    def _reconfigure_pivot(
        self,
        new_pivot_art: tuple[float, float],
        new_pivot_world: tuple[float, float],
    ) -> None:
        """Move the pivot to ``new_pivot_art`` in art frame (attach
        point on the body) and ``new_pivot_world`` in screen coords,
        preserving the buddy's current visual rotation.

        The world-space rotation in effect right before the switch
        (``sim.theta + angle_of_com_offset(old_pivot)``) must equal the
        rotation right after, so the body doesn't pop. We solve for
        the new θ by inverting
        ``theta_new + angle_of(new_pivot) = theta_old + angle_of(old)``.

        Also handles the inverted-pendulum-equilibrium nudge: θ = ±π
        is unstable but physically stationary (sin(±π) = 0), so we
        perturb by a fraction of a degree to let gravity take over.
        """
        old_angle_offset = self._angle_of_com_offset()
        theta_visual = self._sim.theta + old_angle_offset

        self._pivot_art = new_pivot_art
        self._recompute_geometry()
        new_angle_offset = self._angle_of_com_offset()
        new_theta = theta_visual - new_angle_offset
        new_theta = (new_theta + math.pi) % (2 * math.pi) - math.pi
        if math.pi - abs(new_theta) < _UNSTABLE_EPS:
            new_theta = math.copysign(math.pi - _UNSTABLE_EPS, new_theta)

        self._sim.reset_angle(theta=new_theta, theta_dot=self._sim.theta_dot)
        self._sim.set_length(self._pendulum_length(self._pivot_art))
        self._sim.snap_pivot(new_pivot_world[0], new_pivot_world[1])
        self._move_to_pivot()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_active:
            return
        cursor = event.globalPosition()
        now = time.monotonic()
        self._sim.set_pivot(cursor.x(), cursor.y())
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
        self._inject_fling_impulse()
        self._fling_samples.clear()
        self._maybe_edge_dock()
        # Shift the pivot back to the head so gravity pulls the buddy
        # upright during the settle, regardless of where he was grabbed.
        # Visual pose is preserved at the moment of re-pivot — only the
        # anchor moves, body rotates to upright under its own physics.
        self._re_pivot_to_neutral()
        # Keep the timer running — the body may still be swinging.

    def _re_pivot_to_neutral(self) -> None:
        """Re-anchor ``_pivot_art`` at the art's head on release while
        preserving the buddy's current visual pose AND world position.
        Gravity then pulls him upright from wherever he was.

        The new pivot_world is the head's *current* world position
        (not the release cursor) — otherwise swapping pivot_art from
        foot to head would shift the whole body by ~art_height pixels.
        """
        head_pivot_art = (self._art_w / 2.0, 0.0)
        if self._pivot_art == head_pivot_art:
            return
        head_world = self.head_world_position()
        self._reconfigure_pivot(
            new_pivot_art=head_pivot_art,
            new_pivot_world=(head_world.x(), head_world.y()),
        )

    def _inject_fling_impulse(self) -> None:
        """Convert recent cursor motion into an angular impulse.

        The tangential (perpendicular-to-the-rod) component of the
        cursor's velocity at the pivot, divided by the pendulum length,
        gives the angular velocity needed to keep the body's inertia
        carrying through past the release point. The radial component
        is discarded — it doesn't swing the body, it just stretches
        the (rigid) rod.
        """
        if len(self._fling_samples) < 2:
            return
        t0, p0 = self._fling_samples[0]
        t1, p1 = self._fling_samples[-1]
        span = max(t1 - t0, 1e-3)
        vx = (p1.x() - p0.x()) / span
        vy = (p1.y() - p0.y()) / span
        # Tangential direction at current theta in world coords.
        # Body is at pivot + L * (sin θ, cos θ); tangent to that circle
        # (dθ direction) is (cos θ, -sin θ).
        theta = self._sim.theta + self._angle_of_com_offset()
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        v_tangential = vx * cos_t - vy * sin_t
        length = max(self._sim.length, 1.0)
        # Match the forcing sign in PendulumSimulator.tick: a rightward
        # cursor flick (v_tangential > 0 at θ=0) should continue the
        # feet-leftward swing after release, so the impulse adds
        # positively to θ_dot. Scale by the config's fling_scale to
        # keep the impulse punchy without producing multi-revolution
        # spins.
        scale = self._sim.config.fling_scale
        self._sim.apply_angular_impulse(scale * v_tangential / length)

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
        """Snap the pivot to the nearest screen edge when dropped close
        to one. Keeps the buddy feeling "sticky" to monitor boundaries
        and covers the multi-monitor case via QScreen lookup at the
        pivot's current position."""
        pivot_x, pivot_y = self._sim.pivot
        screen = QGuiApplication.screenAt(QPoint(int(pivot_x), int(pivot_y)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        new_x, new_y = pivot_x, pivot_y
        if pivot_x - geom.left() < _EDGE_DOCK_THRESHOLD:
            new_x = geom.left()
        elif geom.right() - pivot_x < _EDGE_DOCK_THRESHOLD:
            new_x = geom.right()
        if pivot_y - geom.top() < _EDGE_DOCK_THRESHOLD:
            new_y = geom.top()
        elif geom.bottom() - pivot_y < _EDGE_DOCK_THRESHOLD:
            new_y = geom.bottom()
        if (new_x, new_y) != (pivot_x, pivot_y):
            self._sim.set_pivot(new_x, new_y)
            self._move_to_pivot()

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
        pwx, pwy = self._pivot_widget
        # Magenta crosshair at the pivot point (= cursor during drag).
        pen = QColor("magenta")
        painter.setPen(pen)
        painter.drawLine(pwx - 12, pwy, pwx + 12, pwy)
        painter.drawLine(pwx, pwy - 12, pwx, pwy + 12)

        cursor = QCursor.pos()
        vx, vy = sim.pivot_vel
        speed = math.hypot(vx, vy)
        px, py = sim.pivot
        state = (
            "DRAG" if self._drag_active
            else ("SLEEP" if sim.sleeping else "swing")
        )
        lines = [
            f"theta {math.degrees(sim.theta):+6.1f}deg  "
            f"w {sim.theta_dot:+5.2f}rad/s",
            f"pivot  ({px:6.0f}, {py:6.0f})",
            f"cursor ({cursor.x():6d}, {cursor.y():6d})",
            f"|v|    {speed:5.0f} px/s  ({vx:+5.0f}, {vy:+5.0f})",
            f"L {sim.length:5.1f}  state {state}",
        ]
        debug_font = QFont("Menlo", 9)
        debug_font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(debug_font)
        fm = painter.fontMetrics()
        line_h = fm.height()
        text_x = pwx + 16
        text_y = pwy + 16 + fm.ascent()
        # Slightly translucent backdrop so text stays readable over
        # whatever window is behind the buddy.
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
        vx, vy = sim.pivot_vel
        cursor = QCursor.pos()
        state = (
            "DRAG" if self._drag_active
            else ("SLEEP" if sim.sleeping else "swing")
        )
        self._debug_log_fp.write(
            f"t={time.monotonic():.3f} "
            f"theta={math.degrees(sim.theta):+7.2f} "
            f"w={sim.theta_dot:+6.3f} "
            f"pivot=({sim.pivot[0]:.1f},{sim.pivot[1]:.1f}) "
            f"cursor=({cursor.x()},{cursor.y()}) "
            f"v=({vx:+.1f},{vy:+.1f}) "
            f"L={sim.length:.1f} "
            f"{state}\n",
        )

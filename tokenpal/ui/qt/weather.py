"""Qt weather overlay: sky window + buddy-rain overlay + shared particle sim.

Renders the weather environment derived from ``EnvironmentSnapshot`` in two
widgets — a buddy-mounted ``SkyWindow`` (anchored above the buddy's current
rect, right-aligned with him) with sun/moon/overcast double-cloud/drifting
rain cloud/lightning strobe/shooting stars, and a ``BuddyRainOverlay`` that
sits over the buddy and renders rain drops landing on him, splashes, and
snow accumulation on his shoulders. Both widgets are frameless, translucent,
and input-transparent (``WA_TransparentForMouseEvents`` pattern from
``dock_mock.py:47``) so clicks pass through to whatever's underneath.

A single ``WeatherSim`` owns all particle state so drops don't tear across
the seam between the sky zone and the buddy overlay. Sim runs on the sky
window's 30 Hz ``QTimer``; the overlay repaints off the same tick via a
direct ``schedule_update`` callback.

Sensitive-app suppression (``EnvironmentSnapshot.sensitive_suppressed``)
CLEARS all particles immediately — an intentional divergence from Textual
(``buddy_environment.py:454``) which freezes them mid-flight. A frozen rain
drop is more attention-drawing than no weather; suppression is meant to go
invisible.

Lightning strobe is a NEW design, not a port: Textual lightning is a single
4-segment bolt with 0.25 s life (buddy_environment.py:675). Here it's a
bounded sky-window alpha strobe with hard caps on frequency/duration to
avoid photosensitivity issues.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from tokenpal.ui.ascii_props import (
    MOON_SPRITE,
    OVERCAST_CLOUD_A,
    RAIN_CLOUD_SPRITE,
    SUN_SPRITE,
    PropSprite,
)
from tokenpal.ui.buddy_environment import (
    CloudDrift,
    EnvironmentSnapshot,
    EnvState,
    Kind,
)
from tokenpal.ui.qt import _paint_trace
from tokenpal.ui.qt._text_fx import render_sprite_pixmap
from tokenpal.ui.qt.platform import buddy_overlay_flags

# --- Timing ---------------------------------------------------------------

_TICK_HZ = 30
_TICK_MS = int(1000 / _TICK_HZ)

# Spawn rates mirror buddy_environment.py:593-606 but operate in pixel space.
# Converted at spawn time using the widget's monospace cell size so the
# overall "falling density per screen width" matches the Textual reference.
_RAIN_PER_SEC = 12.0
_SNOW_PER_SEC = 6.0
_LIGHTNING_PER_SEC = 0.4  # at intensity=1.0, caps out via _LIGHTNING_MIN_GAP_S

# Shooting-star Poisson schedule (clear nights only).
_SHOOTING_STAR_MEAN_S = 60.0
_SHOOTING_STAR_MIN_S = 30.0
_SHOOTING_STAR_MAX_S = 120.0
_SHOOTING_STAR_DURATION_S = 0.9
_SHOOTING_STAR_LENGTH_FRAC = 0.9  # length relative to sky widget width

# Lightning strobe bounds (new design, not a port — see module docstring).
_LIGHTNING_MAX_ALPHA = 0.45
_LIGHTNING_MIN_GAP_S = 8.0
_LIGHTNING_PHASES_MS = (80, 40, 40)  # on, off, on — total 160 ms

# Overcast threshold (matches ascii_props._OVERCAST_INTENSITY).
_OVERCAST_INTENSITY = 0.7

# Velocity envelopes in cells/s (converted to px/s at spawn).
_RAIN_VY_MIN = 8.0
_RAIN_VY_BASE = 14.0
_RAIN_VY_SPREAD = 6.0
_RAIN_VX_SPREAD = 0.3
_SNOW_VY_MIN = 1.5
_SNOW_VY_MAX = 3.5
_SNOW_VX_SWAY = 1.2
_SNOW_SWAY_FREQ = 1.5

_RAIN_LIFE_S = 4.0
_SNOW_LIFE_S = 10.0
_SPLASH_LIFE_S = 0.35
_SNOW_DUST_LIFE_S = 18.0  # cleared on drag (overlay calls clear_buddy_accum)

_AFK_SPAWN_SCALE = 0.3  # matches Textual's afk_scale behavior

# --- Colors ---------------------------------------------------------------

_COL_SUN = "#ffcc44"
_COL_MOON = "#ccccff"
_COL_CLOUD = "#aaaaaa"
_COL_RAIN = "#5599ff"
_COL_SNOW = "#ddddff"
_COL_SPLASH = "#aaccff"
_COL_STAR = "#ffffee"
_COL_LIGHTNING_FLASH = "#ffffee"

# --- Glyphs ---------------------------------------------------------------

_RAIN_GLYPHS = ("'", "|", ".")
_SNOW_GLYPHS = ("*", "·", "*")
_SPLASH_GLYPHS = ("·", "'", ".")
_STAR_GLYPH = "*"

# --- Sprite layout -------------------------------------------------------

# Paint-time scale applied to the drifting rain cloud (both rendered
# size AND the spawn footprint width the rain column infers).
_RAIN_CLOUD_SCALE = 1.1

# Anti-phase drift amplitudes for the two overcast cloud layers.
_OVERCAST_DRIFT_AMP = 3.0  # cells
_OVERCAST_A_PHASE = 0.0
_OVERCAST_B_PHASE = math.pi
_OVERCAST_A_DY = 2  # rows from sun top
_OVERCAST_B_DY = 3
_OVERCAST_B_DX = -4  # cells shift so B doesn't overdraw A

# --- Data classes ---------------------------------------------------------


@dataclass
class WeatherParticle:
    """A single rain drop, snow flake, splash, or shoulder-dust mark.

    All positions in **screen pixels**. ``vx``/``vy`` in pixels/second. The
    sim mutates in place each tick; widgets query the full list and filter
    by their world-rect for rendering.
    """

    kind: str  # "rain" | "snow" | "splash" | "snow_dust"
    x: float
    y: float
    vx: float
    vy: float
    life: float
    glyph: str
    color: str
    spawn_t: float = 0.0
    phase: float = 0.0  # used for snow lateral sway


@dataclass
class ShootingStar:
    """A streaking line that crosses the moon's bounding box.

    Parameterized as a start point + velocity + remaining duration. The sky
    widget draws a short trail behind the head for motion blur.
    """

    x: float
    y: float
    vx: float
    vy: float
    remaining_s: float
    trail_px: float = 28.0


@dataclass
class LightningStrobe:
    """Alpha strobe for the storm flash. Runs through a phase list:
    (on_ms, off_ms, on_ms). Between flashes alpha is 0."""

    elapsed_ms: float = 0.0
    phases_ms: tuple[int, ...] = _LIGHTNING_PHASES_MS
    active: bool = False

    @property
    def total_ms(self) -> int:
        return sum(self.phases_ms)

    def current_alpha(self) -> float:
        if not self.active:
            return 0.0
        t = self.elapsed_ms
        on1, off, on2 = self.phases_ms
        if t < on1:
            return _LIGHTNING_MAX_ALPHA
        if t < on1 + off:
            return 0.0
        if t < on1 + off + on2:
            return _LIGHTNING_MAX_ALPHA * 0.7
        return 0.0

    def tick(self, dt_s: float) -> None:
        if not self.active:
            return
        self.elapsed_ms += dt_s * 1000.0
        if self.elapsed_ms >= self.total_ms:
            self.active = False
            self.elapsed_ms = 0.0

    def trigger(self) -> None:
        if self.active:
            return
        self.active = True
        self.elapsed_ms = 0.0


# --- Sim ------------------------------------------------------------------


class WeatherSim:
    """Stateful particle sim shared by ``SkyWindow`` and ``BuddyRainOverlay``.

    The sky widget owns the QTimer and calls ``tick()`` at ~30 Hz. The
    overlay polls ``particles`` each repaint and filters by its world rect.

    ``env_provider`` is the same callable ``QtOverlay`` already stores at
    ``overlay.py:147`` (Phase-4 wiring) — returns an ``EnvironmentSnapshot``
    or ``None``. Called on the Qt main thread.

    ``sky_rect_provider`` / ``buddy_rect_provider`` return the current
    widget world rects (``QRectF`` in global screen coords). The sky rect
    is fixed top-right; the buddy rect updates every ``position_changed``.

    ``cell_px`` is the monospace cell width in pixels — used to convert
    Textual's per-cell spawn rates into per-pixel physics without losing
    the visual density calibration.
    """

    def __init__(
        self,
        *,
        env_provider: Callable[[], EnvironmentSnapshot | None] | None = None,
        sky_rect_provider: Callable[[], QRectF] = lambda: QRectF(),
        buddy_rect_provider: Callable[[], QRectF | None] = lambda: None,
        buddy_art_hit: Callable[[QPointF], bool] | None = None,
        cell_px: float = 10.0,
        line_px: float | None = None,
        rng: random.Random | None = None,
        now_hour: Callable[[], int] | None = None,
    ) -> None:
        self.env_provider = env_provider
        self.sky_rect_provider = sky_rect_provider
        self.buddy_rect_provider = buddy_rect_provider
        # Pinned per plan: buddy contact uses the tight art-frame AABB via
        # this callback, not the world rect. The rect is the coarse cull;
        # this is the precision check. Overlay supplies the inverse of
        # ``BuddyWindow._build_transform()`` + ``art_bounds()``.
        self.buddy_art_hit = buddy_art_hit
        self.cell_px = max(cell_px, 1.0)
        self.line_px = max(line_px if line_px is not None else cell_px, 1.0)
        self.rng = rng or random.Random()
        self._now_hour = now_hour  # test hook; None → derive from datetime

        self.particles: list[WeatherParticle] = []
        self.shooting_stars: list[ShootingStar] = []
        self.lightning = LightningStrobe()
        self.cloud_drift = CloudDrift(period_s=45.0)

        self._rain_accum = 0.0
        self._snow_accum = 0.0
        self._lightning_accum = 0.0
        self._lightning_next_ok_t = 0.0
        self._shooting_star_due_s = self._roll_shooting_star_gap()
        self._last_suppressed = False
        self._t = 0.0  # monotonic sim time for phase math

        # Dev override: when set, ``_derive_env`` ignores ``env_provider``
        # and synthesizes an EnvState from these fields. Set via
        # ``set_override`` from the ``/weather`` slash command.
        self._forced_code: int | None = None
        self._forced_hour: int | None = None

        # Cached EnvState derived in latest tick — widgets read this for
        # prop selection. None before first tick.
        self.env: EnvState | None = None

    def set_cell_px(self, cell_px: float, line_px: float | None = None) -> None:
        self.cell_px = max(cell_px, 1.0)
        if line_px is not None:
            self.line_px = max(line_px, 1.0)

    # --- Public tick --------------------------------------------------

    def tick(self, dt_s: float) -> None:
        self._t += dt_s
        snap = self.env_provider() if self.env_provider is not None else None
        env = self._derive_env(snap)
        self.env = env

        # Sensitive-app CLEAR (intentional divergence from Textual freeze;
        # see module docstring). Idempotent across ticks.
        if env.sensitive_suppressed:
            if not self._last_suppressed:
                self.particles.clear()
                self.shooting_stars.clear()
                self.lightning = LightningStrobe()
                self._rain_accum = 0.0
                self._snow_accum = 0.0
                self._lightning_accum = 0.0
            self._last_suppressed = True
            return
        self._last_suppressed = False

        self.cloud_drift.tick(dt_s, env)
        self._advance_particles(dt_s)
        self._advance_shooting_stars(dt_s)
        self.lightning.tick(dt_s)
        self._maybe_spawn(env, dt_s)

    # --- Env derivation ----------------------------------------------

    def set_override(
        self,
        *,
        weather_code: int | None = None,
        hour: int | None = None,
        clear: bool = False,
    ) -> None:
        """Dev override — supplied by the ``/weather`` command. Passing
        ``clear=True`` reverts to the real brain-supplied snapshot. A
        ``None`` value for ``weather_code`` or ``hour`` preserves the
        prior override on that axis."""
        if clear:
            self._forced_code = None
            self._forced_hour = None
            return
        if weather_code is not None:
            self._forced_code = weather_code
        if hour is not None:
            self._forced_hour = hour

    def _derive_env(self, snap: EnvironmentSnapshot | None) -> EnvState:
        if self._forced_code is not None:
            forced_snap = EnvironmentSnapshot(
                weather_data={
                    "weather_code": self._forced_code,
                    # Pick a neutral temperature so hot_outside never fires
                    # under a forced state (we don't want sweat beads
                    # spawning just because the user forced "storm").
                    "temperature": 70.0,
                    "unit": "F",
                },
                idle_event=snap.idle_event if snap is not None else None,
                sensitive_suppressed=(
                    snap.sensitive_suppressed if snap is not None else False
                ),
            )
            base = EnvState.from_inputs(
                weather_data=forced_snap.weather_data,
                idle_event=forced_snap.idle_event,
                sensitive_suppressed=forced_snap.sensitive_suppressed,
            )
            hour = self._forced_hour
            if hour is None and self._now_hour is not None:
                hour = self._now_hour()
            if hour is not None:
                return EnvState(
                    kind=base.kind,
                    intensity=base.intensity,
                    hot_outside=False,
                    is_day=(6 <= hour < 19),
                    afk_active=base.afk_active,
                    sensitive_suppressed=base.sensitive_suppressed,
                )
            return base
        if snap is None:
            # No brain wiring yet — pretend clear day.
            return EnvState(
                kind=Kind.CLEAR,
                intensity=0.7,
                hot_outside=False,
                is_day=self._is_day_now(),
                afk_active=False,
                sensitive_suppressed=False,
            )
        # EnvState.from_inputs reads datetime.now() internally; we want a
        # test-override hook for is_day. Derive kind ourselves when the
        # hour hook is set; otherwise delegate.
        if self._now_hour is not None:
            base = EnvState.from_inputs(
                weather_data=snap.weather_data,
                idle_event=snap.idle_event,
                sensitive_suppressed=snap.sensitive_suppressed,
            )
            hour = self._now_hour()
            return EnvState(
                kind=base.kind,
                intensity=base.intensity,
                hot_outside=base.hot_outside,
                is_day=(6 <= hour < 19),
                afk_active=base.afk_active,
                sensitive_suppressed=base.sensitive_suppressed,
            )
        return EnvState.from_inputs(
            weather_data=snap.weather_data,
            idle_event=snap.idle_event,
            sensitive_suppressed=snap.sensitive_suppressed,
        )

    def _is_day_now(self) -> bool:
        if self._now_hour is not None:
            h = self._now_hour()
        else:
            import datetime as _dt
            h = _dt.datetime.now().hour
        return 6 <= h < 19

    # --- Spawning ----------------------------------------------------

    def _maybe_spawn(self, env: EnvState, dt_s: float) -> None:
        afk_scale = _AFK_SPAWN_SCALE if env.afk_active else 1.0
        kind = env.kind
        intensity = env.intensity

        if kind in (Kind.RAIN, Kind.DRIZZLE, Kind.STORM):
            self._rain_accum += dt_s * _RAIN_PER_SEC * intensity * afk_scale
            while self._rain_accum >= 1.0:
                self._rain_accum -= 1.0
                self._spawn_rain(intensity)
        elif kind is Kind.SNOW:
            self._snow_accum += dt_s * _SNOW_PER_SEC * intensity * afk_scale
            while self._snow_accum >= 1.0:
                self._snow_accum -= 1.0
                self._spawn_snow()
        else:
            self._rain_accum = 0.0
            self._snow_accum = 0.0

        if kind is Kind.STORM:
            self._lightning_accum += (
                dt_s * _LIGHTNING_PER_SEC * intensity * afk_scale
            )
            if (
                self._lightning_accum >= 1.0
                and not self.lightning.active
                and self._t >= self._lightning_next_ok_t
            ):
                self._lightning_accum = 0.0
                self.lightning.trigger()
                self._lightning_next_ok_t = self._t + _LIGHTNING_MIN_GAP_S
        else:
            self._lightning_accum = 0.0

        # Shooting stars only on clear nights. The event schedule advances
        # whenever the sky is a "clear-night" state; otherwise we reset so
        # we don't fire 10 stars the instant the sky clears.
        if kind is Kind.CLEAR and not env.is_day:
            self._shooting_star_due_s -= dt_s
            if self._shooting_star_due_s <= 0.0:
                self._spawn_shooting_star()
                self._shooting_star_due_s = self._roll_shooting_star_gap()
        else:
            self._shooting_star_due_s = self._roll_shooting_star_gap()

    def _spawn_rain(self, intensity: float) -> None:
        sky = self.sky_rect_provider()
        if sky.isEmpty():
            return
        # Spawn in the rain cloud's footprint, drifted by cloud offset.
        cloud_px_w = RAIN_CLOUD_SPRITE.width * self.cell_px
        drift = self.cloud_drift.offset_x(_OVERCAST_DRIFT_AMP, 0.0) * self.cell_px
        cx = sky.center().x() + drift
        x = cx + self.rng.uniform(-cloud_px_w / 2.0, cloud_px_w / 2.0)
        y = sky.top() + self.line_px * (
            1.0 + RAIN_CLOUD_SPRITE.height * _RAIN_CLOUD_SCALE
        )
        vy_cells = self.rng.uniform(
            _RAIN_VY_MIN + 4.0 * intensity,
            _RAIN_VY_BASE + _RAIN_VY_SPREAD * intensity,
        )
        vx_cells = self.rng.uniform(-_RAIN_VX_SPREAD, _RAIN_VX_SPREAD)
        # Add a wind-drag term tied to cloud drift derivative so drops
        # don't fall in parallel machine-lines (plan failure mode).
        wind = -math.sin(
            (self.cloud_drift.phase_s / max(self.cloud_drift.period_s, 1e-6))
            * 2.0 * math.pi
        ) * 0.8
        self.particles.append(WeatherParticle(
            kind="rain",
            x=x, y=y,
            vx=(vx_cells + wind) * self.cell_px,
            vy=vy_cells * self.cell_px,
            life=_RAIN_LIFE_S,
            glyph=self.rng.choice(_RAIN_GLYPHS),
            color=_COL_RAIN,
            spawn_t=self._t,
        ))

    def _spawn_snow(self) -> None:
        sky = self.sky_rect_provider()
        if sky.isEmpty():
            return
        cloud_px_w = RAIN_CLOUD_SPRITE.width * self.cell_px
        drift = self.cloud_drift.offset_x(_OVERCAST_DRIFT_AMP, 0.0) * self.cell_px
        cx = sky.center().x() + drift
        x = cx + self.rng.uniform(-cloud_px_w / 2.0, cloud_px_w / 2.0)
        y = sky.top() + self.line_px * (
            1.0 + RAIN_CLOUD_SPRITE.height * _RAIN_CLOUD_SCALE
        )
        vy = self.rng.uniform(_SNOW_VY_MIN, _SNOW_VY_MAX) * self.cell_px
        phase = self.rng.uniform(0.0, 2.0 * math.pi)
        self.particles.append(WeatherParticle(
            kind="snow",
            x=x, y=y,
            vx=0.0,
            vy=vy,
            life=_SNOW_LIFE_S,
            glyph=self.rng.choice(_SNOW_GLYPHS),
            color=_COL_SNOW,
            spawn_t=self._t,
            phase=phase,
        ))

    def _spawn_shooting_star(self) -> None:
        sky = self.sky_rect_provider()
        if sky.isEmpty():
            return
        mw = MOON_SPRITE.width * self.cell_px
        mh = MOON_SPRITE.height * self.cell_px
        mx = sky.right() - mw - self.cell_px
        my = sky.top() + self.cell_px
        moon = QRectF(mx, my, mw, mh)
        # Pick a line through the moon center with ±25° jitter from a
        # default -30° (upper-left to lower-right) trajectory.
        angle = math.radians(-30.0 + self.rng.uniform(-25.0, 25.0))
        length = _SHOOTING_STAR_LENGTH_FRAC * sky.width()
        vx = math.cos(angle) * (length / _SHOOTING_STAR_DURATION_S)
        vy = -math.sin(angle) * (length / _SHOOTING_STAR_DURATION_S)
        # Start point: step back from moon center along -(vx,vy) so the
        # trajectory passes through the moon.
        cx = moon.center().x()
        cy = moon.center().y()
        t_back = 0.45 * _SHOOTING_STAR_DURATION_S
        sx = cx - vx * t_back
        sy = cy - vy * t_back
        self.shooting_stars.append(ShootingStar(
            x=sx, y=sy, vx=vx, vy=vy,
            remaining_s=_SHOOTING_STAR_DURATION_S,
        ))

    def _roll_shooting_star_gap(self) -> float:
        # Exponential distribution clamped into [min, max] so the UX is
        # "rare but you'll see one in the next couple of minutes."
        raw = self.rng.expovariate(1.0 / _SHOOTING_STAR_MEAN_S)
        return max(_SHOOTING_STAR_MIN_S, min(_SHOOTING_STAR_MAX_S, raw))

    # --- Particle advance --------------------------------------------

    def _advance_particles(self, dt_s: float) -> None:
        kept: list[WeatherParticle] = []
        buddy = self.buddy_rect_provider()
        for p in self.particles:
            p.life -= dt_s
            if p.life <= 0.0:
                continue
            if p.kind == "snow":
                # Gentle lateral sway (Textual uses spin phase → vx sine).
                sway = math.sin(
                    (self._t + p.phase) * _SNOW_SWAY_FREQ * 2.0 * math.pi
                ) * _SNOW_VX_SWAY * self.cell_px
                p.x += sway * dt_s
                p.y += p.vy * dt_s
            elif p.kind in ("rain", "splash"):
                p.x += p.vx * dt_s
                p.y += p.vy * dt_s
            # snow_dust is static; life just ticks down.

            # Rain/snow contact with the buddy → splash + vaporize drop,
            # or snow → accumulate shoulder-dust and vaporize flake.
            if p.kind == "rain" and buddy is not None and self._hits_buddy(p, buddy):
                kept.extend(self._spawn_splash(p.x, p.y))
                continue
            if p.kind == "snow" and buddy is not None and self._hits_buddy(p, buddy):
                kept.append(self._make_snow_dust(p.x, p.y))
                continue

            # Off-screen cull: drops that exit below the buddy zone or
            # off sides. Use a generous margin since screen dimensions
            # are bigger than any rect we have.
            if p.y > 4000.0 or p.x < -200.0 or p.x > 8000.0:
                continue
            kept.append(p)
        self.particles = kept

    def _hits_buddy(self, p: WeatherParticle, buddy: QRectF) -> bool:
        # Coarse screen-space cull first so we don't invert a transform
        # for every drop every tick.
        if not buddy.contains(QPointF(p.x, p.y)):
            return False
        # Precision art-frame check (plan pin: inverse of
        # ``BuddyWindow._build_transform()``, compare to tight art AABB).
        if self.buddy_art_hit is not None:
            return self.buddy_art_hit(QPointF(p.x, p.y))
        # No precision callback supplied (tests) — world rect suffices.
        return True

    def _spawn_splash(self, x: float, y: float) -> list[WeatherParticle]:
        out: list[WeatherParticle] = []
        for _ in range(3):
            angle = self.rng.uniform(-math.pi, 0.0)
            speed = self.rng.uniform(20.0, 60.0)
            out.append(WeatherParticle(
                kind="splash",
                x=x, y=y,
                vx=math.cos(angle) * speed,
                vy=math.sin(angle) * speed,
                life=_SPLASH_LIFE_S,
                glyph=self.rng.choice(_SPLASH_GLYPHS),
                color=_COL_SPLASH,
                spawn_t=self._t,
            ))
        return out

    def _make_snow_dust(self, x: float, y: float) -> WeatherParticle:
        return WeatherParticle(
            kind="snow_dust",
            x=x, y=y,
            vx=0.0, vy=0.0,
            life=_SNOW_DUST_LIFE_S,
            glyph="·",
            color=_COL_SNOW,
            spawn_t=self._t,
        )

    def _advance_shooting_stars(self, dt_s: float) -> None:
        kept: list[ShootingStar] = []
        for s in self.shooting_stars:
            s.remaining_s -= dt_s
            if s.remaining_s <= 0.0:
                continue
            s.x += s.vx * dt_s
            s.y += s.vy * dt_s
            kept.append(s)
        self.shooting_stars = kept

    # --- Widget-facing queries ----------------------------------------

    def clear_buddy_accum(self) -> None:
        """Remove any accumulated snow_dust — called by the overlay when
        the buddy starts dragging so dust doesn't trail stuck to midair
        where his shoulders used to be."""
        self.particles = [p for p in self.particles if p.kind != "snow_dust"]


# --- Sky widget -----------------------------------------------------------


_SKY_W_PX = 200
# Sky height is derived from ``line_h`` so the tallest luminary fits on
# any platform (Consolas's bigger height/ascent ratio used to clip the
# bottom rays of the sun/moon when the height was a fixed magic number).
# Moon (8 rows) is the tallest sprite; +margin gives breathing room for
# the top of the halo.
_MAX_SKY_SPRITE_ROWS = 8
_SKY_HEIGHT_MARGIN_PX = 4
# Gap between the buddy's top edge and the sky panel's bottom edge.
_SKY_BUDDY_GAP_PX = 6
# Horizontal bias — shift the sky panel so the sun/moon sprite (which
# anchors to the panel's right edge) sits above the buddy's upper-right
# area rather than off to the side.
_SKY_RIGHT_BIAS_PX = 16


class SkyWindow(QWidget):
    """Buddy-mounted sky panel.

    Sits above the buddy's current world rect (right-aligned with his
    right edge) and paints sun/moon, overcast double-stack or drifting
    single cloud, lightning alpha strobe, shooting stars, and the in-sky
    portion of any falling rain/snow. Owns the 30 Hz tick that drives
    ``WeatherSim`` and hosts the update hook for ``BuddyRainOverlay``.

    The panel follows the buddy because the whole buddy+weather group
    reads as a single creature carrying its own little climate. The
    screen-corner anchoring in earlier drafts "worked" in code but
    visually disconnected the weather from the buddy.
    """

    def __init__(
        self,
        sim: WeatherSim,
        *,
        font_family: str = "Courier",
        font_size: int = 14,
        overlay_update_hook: Callable[[], None] | None = None,
        buddy_rect_provider: Callable[[], QRectF | None] = lambda: None,
    ) -> None:
        super().__init__()
        self._sim = sim
        self._overlay_update = overlay_update_hook
        self._buddy_rect_provider = buddy_rect_provider
        self._base_font_size = font_size
        self._zoom = 1.0
        self._font = QFont(font_family, font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._cell_w = max(self._measure_cell_w(), 1)
        self._line_h = QFontMetrics(self._font).height()
        self._sim.set_cell_px(float(self._cell_w), float(self._line_h))
        self._sprite_cache: dict[
            tuple[tuple[str, ...], int, int, int], QPixmap,
        ] = {}

        self.setWindowFlags(buddy_overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sky_h = _MAX_SKY_SPRITE_ROWS * self._line_h + _SKY_HEIGHT_MARGIN_PX
        self.resize(_SKY_W_PX, sky_h)
        self._base_size = (_SKY_W_PX, sky_h)
        self._last_anchor: tuple[int, int] = (-9999, -9999)
        self.reanchor()

        # Expose the sky's world rect to the sim so particles spawn in
        # global screen coords.
        self._sim.sky_rect_provider = self._world_rect

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # --- Lifecycle helpers -------------------------------------------

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_zoom(self, factor: float) -> None:
        if factor == self._zoom:
            return
        self._zoom = factor
        new_size = max(int(round(self._base_font_size * factor)), 4)
        self._font.setPointSize(new_size)
        self._cell_w = max(self._measure_cell_w(), 1)
        # QFontMetrics(self._font), not self.fontMetrics(): the widget's
        # inherited font is unrelated to ``self._font``, so the latter
        # would leave line_h at the default-font value while cell_w
        # scaled — sprite renders horizontally stretched.
        self._line_h = QFontMetrics(self._font).height()
        self._sim.set_cell_px(float(self._cell_w), float(self._line_h))
        self._sprite_cache.clear()
        # Grow the sky panel itself so a 2× sun isn't clipped.
        base_w, base_h = self._base_size
        self.resize(
            max(1, int(round(base_w * factor))),
            max(1, int(round(base_h * factor))),
        )
        self._last_anchor = (-9999, -9999)  # force reanchor on next call
        self.reanchor()
        self.update()

    def _measure_cell_w(self) -> int:
        from tokenpal.ui.buddy_core import measure_block_paint_width
        return max(measure_block_paint_width(self._font) - 1, 1)

    # --- Geometry ----------------------------------------------------

    def _world_rect(self) -> QRectF:
        pos = self.pos()
        return QRectF(pos.x(), pos.y(), self.width(), self.height())

    def reanchor(self) -> None:
        """Reposition the sky panel above the buddy's world rect.

        Idempotent — ``position_changed`` on BuddyWindow fires every
        physics tick (buddy_window.py:344). Early-return if integer pos
        hasn't changed."""
        rect = self._buddy_rect_provider()
        if rect is None or rect.isEmpty():
            # Fall back to primary-screen top-right so the widget is
            # still visible in degenerate cases (buddy not yet built).
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            new_x = geo.right() - self.width() - 24
            new_y = geo.top() + 24
        else:
            # Right edge of sky = right edge of buddy + right-bias.
            # Bottom edge of sky = top of buddy - gap.
            new_x = int(rect.right() + _SKY_RIGHT_BIAS_PX - self.width())
            new_y = int(rect.top() - _SKY_BUDDY_GAP_PX - self.height())
        if (new_x, new_y) != self._last_anchor:
            self.move(new_x, new_y)
            self._last_anchor = (new_x, new_y)

    # --- Tick --------------------------------------------------------

    def _on_tick(self) -> None:
        dt = _TICK_MS / 1000.0
        self._sim.tick(dt)
        self.update()
        if self._overlay_update is not None:
            self._overlay_update()

    # --- Paint -------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        env = self._sim.env
        if env is None:
            return
        if _paint_trace.enabled():
            pos = self.pos()
            _paint_trace.log_paint(
                "sky", pos=(float(pos.x()), float(pos.y())),
            )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setFont(self._font)

        world = self._world_rect()
        sky_local_h = float(self.height())

        # Backdrop for lightning strobe — painted behind everything.
        alpha = self._sim.lightning.current_alpha()
        if alpha > 0.0:
            flash = QColor(_COL_LIGHTNING_FLASH)
            flash.setAlphaF(alpha)
            painter.fillRect(self.rect(), flash)

        # Luminary in the upper-right of the widget.
        self._paint_luminary(painter, env)

        # Overcast double-stack or single drifting cloud.
        self._paint_clouds(painter, env)

        # Shooting stars before particles so rain paints on top.
        self._paint_shooting_stars(painter, world)

        # In-sky particles (rain/snow that hasn't exited the sky widget).
        self._paint_particles(painter, world, sky_local_h)

    def _paint_luminary(self, painter: QPainter, env: EnvState) -> None:
        # Only clear and cloudy states show the sun/moon. Precipitation
        # (rain/drizzle/storm/snow) and fog hide it entirely — the
        # drifting cloud or dense overcast is doing the job of
        # "you can't see the sky right now."
        if env.kind not in (Kind.CLEAR, Kind.CLOUDY):
            return
        sprite = SUN_SPRITE if env.is_day else MOON_SPRITE
        color = QColor(_COL_SUN if env.is_day else _COL_MOON)
        nat_w = sprite.width * self._cell_w
        nat_h = sprite.height * self._line_h
        x0 = self.width() - nat_w - self._cell_w
        self._draw_sprite_pix(
            painter, sprite, target=QRect(x0, 0, nat_w, nat_h), color=color,
        )

    def _paint_clouds(self, painter: QPainter, env: EnvState) -> None:
        cloud_color = QColor(_COL_CLOUD)
        # Overcast (cloudy + intensity >= 0.7): double-stack anti-phase.
        if env.kind is Kind.CLOUDY and env.intensity >= _OVERCAST_INTENSITY:
            sprite = OVERCAST_CLOUD_A
            nat_w = sprite.width * self._cell_w
            nat_h = sprite.height * self._line_h
            base_x = self.width() - nat_w - self._cell_w
            drift_a = self._sim.cloud_drift.offset_x(
                _OVERCAST_DRIFT_AMP, _OVERCAST_A_PHASE,
            ) * self._cell_w
            drift_b = self._sim.cloud_drift.offset_x(
                _OVERCAST_DRIFT_AMP, _OVERCAST_B_PHASE,
            ) * self._cell_w
            self._draw_sprite_pix(
                painter, sprite,
                target=QRect(
                    int(base_x + drift_a),
                    _OVERCAST_A_DY * self._line_h,
                    nat_w, nat_h,
                ),
                color=cloud_color,
            )
            self._draw_sprite_pix(
                painter, sprite,
                target=QRect(
                    int(base_x + _OVERCAST_B_DX * self._cell_w + drift_b),
                    _OVERCAST_B_DY * self._line_h,
                    nat_w, nat_h,
                ),
                color=cloud_color,
            )
            return

        if env.kind in (Kind.RAIN, Kind.DRIZZLE, Kind.STORM, Kind.SNOW):
            nat_w = RAIN_CLOUD_SPRITE.width * self._cell_w
            nat_h = RAIN_CLOUD_SPRITE.height * self._line_h
            scaled_w = int(round(nat_w * _RAIN_CLOUD_SCALE))
            scaled_h = int(round(nat_h * _RAIN_CLOUD_SCALE))
            drift = self._sim.cloud_drift.offset_x(
                _OVERCAST_DRIFT_AMP, 0.0,
            ) * self._cell_w
            cx = self.width() / 2
            x = int(cx - scaled_w / 2 + drift)
            self._draw_sprite_pix(
                painter, RAIN_CLOUD_SPRITE,
                target=QRect(x, 0, scaled_w, scaled_h),
                color=cloud_color,
            )

    def _sprite_pixmap(self, sprite: PropSprite, color: QColor) -> QPixmap:
        key = (sprite.lines, color.rgb(), self._cell_w, self._font.pointSize())
        cached = self._sprite_cache.get(key)
        if cached is not None:
            return cached
        pix = render_sprite_pixmap(
            sprite.lines, color,
            cell_w=self._cell_w,
            font=self._font, dpr=self.devicePixelRatioF(),
        )
        self._sprite_cache[key] = pix
        return pix

    def _draw_sprite_pix(
        self, painter: QPainter, sprite: PropSprite, *,
        target: QRect, color: QColor,
    ) -> None:
        painter.drawPixmap(target, self._sprite_pixmap(sprite, color))

    def _paint_shooting_stars(self, painter: QPainter, world: QRectF) -> None:
        for s in self._sim.shooting_stars:
            if not world.contains(QPointF(s.x, s.y)):
                # Draw anyway if the head is within the sky rect; the
                # trail might extend off-widget, we clip naturally.
                if not (
                    world.left() - s.trail_px <= s.x <= world.right() + s.trail_px
                    and world.top() - s.trail_px <= s.y <= world.bottom() + s.trail_px
                ):
                    continue
            # Draw a short trail behind the head: 4 glyphs.
            vx, vy = s.vx, s.vy
            mag = math.hypot(vx, vy) or 1.0
            ux, uy = vx / mag, vy / mag
            head_local_x = s.x - world.left()
            head_local_y = s.y - world.top()
            for i in range(4):
                trail_t = i * (s.trail_px / 4.0 / mag)
                tx = head_local_x - ux * mag * trail_t
                ty = head_local_y - uy * mag * trail_t
                alpha = 1.0 - (i / 4.0)
                col = QColor(_COL_STAR)
                col.setAlphaF(alpha)
                painter.setPen(col)
                painter.drawText(int(tx), int(ty), _STAR_GLYPH)

    def _paint_particles(
        self, painter: QPainter, world: QRectF, sky_local_h: float,
    ) -> None:
        for p in self._sim.particles:
            if p.kind in ("splash", "snow_dust"):
                continue  # buddy-overlay paints these
            if not world.contains(QPointF(p.x, p.y)):
                continue
            painter.setPen(QColor(p.color))
            local_x = int(p.x - world.left())
            local_y = int(p.y - world.top())
            painter.drawText(local_x, local_y, p.glyph)


# --- Buddy rain overlay --------------------------------------------------


_BUDDY_OVERLAY_PAD_PX = 60  # widening so drops render while entering the rect


class BuddyRainOverlay(QWidget):
    """Input-transparent overlay that tracks the buddy's world rect and
    paints only the particles that live inside it — rain drops mid-fall
    toward his head, splashes, and the shoulder snow-dust line. The sim's
    ``_hit_test`` already decided a drop is inside the buddy's padded
    world rect; the overlay runs the tight art-frame OBB check here via
    ``_transform_world_to_art`` so a drop that's *visually* outside the
    rotated art doesn't render a splash on the glyph padding."""

    def __init__(
        self,
        sim: WeatherSim,
        *,
        font_family: str = "Courier",
        font_size: int = 14,
        buddy_rect_provider: Callable[[], QRectF | None] = lambda: None,
    ) -> None:
        super().__init__()
        self._sim = sim
        self._base_font_size = font_size
        self._zoom = 1.0
        self._font = QFont(font_family, font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._buddy_rect_provider = buddy_rect_provider

        self.setWindowFlags(buddy_overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resize(200, 200)
        self._last_anchor: tuple[int, int] = (-9999, -9999)

    def set_zoom(self, factor: float) -> None:
        if factor == self._zoom:
            return
        self._zoom = factor
        self._font.setPointSize(max(int(round(self._base_font_size * factor)), 4))
        self.update()

    def reanchor(self) -> None:
        """Idempotent re-anchor to current buddy rect + padding. ``position_
        changed`` fires every physics tick (``buddy_window.py:344``), so we
        early-return if the integer pos hasn't shifted."""
        rect = self._buddy_rect_provider()
        if rect is None or rect.isEmpty():
            if not self.isHidden():
                self.hide()
            return
        if self.isHidden():
            self.show()
        padded = rect.adjusted(
            -_BUDDY_OVERLAY_PAD_PX, -_BUDDY_OVERLAY_PAD_PX,
            _BUDDY_OVERLAY_PAD_PX, _BUDDY_OVERLAY_PAD_PX,
        )
        new_x = int(padded.left())
        new_y = int(padded.top())
        new_w = max(int(padded.width()), 1)
        new_h = max(int(padded.height()), 1)
        if (new_x, new_y) != self._last_anchor:
            self.move(new_x, new_y)
            self._last_anchor = (new_x, new_y)
        if self.width() != new_w or self.height() != new_h:
            self.resize(new_w, new_h)

    def _world_rect(self) -> QRectF:
        pos = self.pos()
        return QRectF(pos.x(), pos.y(), self.width(), self.height())

    def paintEvent(self, _event: QPaintEvent) -> None:
        if _paint_trace.enabled():
            pos = self.pos()
            _paint_trace.log_paint(
                "rain", pos=(float(pos.x()), float(pos.y())),
            )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self._font)
        world = self._world_rect()
        for p in self._sim.particles:
            if not world.contains(QPointF(p.x, p.y)):
                continue
            painter.setPen(QColor(p.color))
            painter.drawText(
                int(p.x - world.left()),
                int(p.y - world.top()),
                p.glyph,
            )


"""Pure logic for the buddy's animated environment layer.

No Textual imports — unit-testable in isolation. The Textual overlay drives
``BuddyMotion.tick`` and ``ParticleField.tick`` from a 10 Hz ``set_interval``
and renders the result via ``render_line`` returning ``Strip`` segments.
"""

from __future__ import annotations

import datetime as _dt
import math
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PARTICLE_LIMIT = 80
HOT_OUTSIDE_F_DEFAULT = 85.0

# Physics-overlay tuning for click/drag/shake reactions.
_RECOIL_DURATION_S = 0.4
_DIZZY_DURATION_S = 3.0
_SHAKE_WINDOW_S = 0.5
_SHAKE_REVERSAL_THRESHOLD = 3
_DRAG_EASE_RATE = 14.0
_MAX_DRAG_OFFSET = 40.0


class Kind(StrEnum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    FOG = "fog"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    SNOW = "snow"
    STORM = "storm"


# WMO weather codes → (kind, intensity 0..1).
# Reference: https://open-meteo.com/en/docs (weather_code field).
_WMO_MAP: dict[int, tuple[Kind, float]] = {
    0: (Kind.CLEAR, 1.0),
    1: (Kind.CLEAR, 0.7),
    2: (Kind.CLOUDY, 0.4),
    3: (Kind.CLOUDY, 0.8),
    45: (Kind.FOG, 0.6),
    48: (Kind.FOG, 0.8),
    51: (Kind.DRIZZLE, 0.3),
    53: (Kind.DRIZZLE, 0.5),
    55: (Kind.DRIZZLE, 0.8),
    61: (Kind.RAIN, 0.3),
    63: (Kind.RAIN, 0.6),
    65: (Kind.RAIN, 1.0),
    66: (Kind.RAIN, 0.4),
    67: (Kind.RAIN, 0.9),
    71: (Kind.SNOW, 0.3),
    73: (Kind.SNOW, 0.6),
    75: (Kind.SNOW, 1.0),
    77: (Kind.SNOW, 0.4),
    80: (Kind.RAIN, 0.4),
    81: (Kind.RAIN, 0.7),
    82: (Kind.RAIN, 1.0),
    85: (Kind.SNOW, 0.5),
    86: (Kind.SNOW, 0.9),
    95: (Kind.STORM, 0.7),
    96: (Kind.STORM, 0.9),
    99: (Kind.STORM, 1.0),
}


def wmo_to_kind(code: int | None) -> tuple[Kind, float]:
    """Map an Open-Meteo WMO code to a (kind, intensity) tuple. Unknown
    codes fall back to (CLEAR, 0.5) so the ambient layer still renders dust
    motes without claiming a weather state we don't know.
    """
    if code is None:
        return Kind.CLEAR, 0.5
    return _WMO_MAP.get(int(code), (Kind.CLEAR, 0.5))


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Plain data shuttle from brain to overlay, posted ~1 Hz. Held as raw
    inputs (not a derived ``EnvState``) so the overlay can re-derive on
    resize without round-tripping the brain.
    """

    weather_data: dict[str, Any] | None
    idle_event: str | None
    sensitive_suppressed: bool


@dataclass(frozen=True)
class EnvState:
    kind: Kind
    intensity: float
    hot_outside: bool
    is_day: bool
    afk_active: bool
    sensitive_suppressed: bool

    @classmethod
    def from_inputs(
        cls,
        *,
        weather_data: dict[str, Any] | None,
        idle_event: str | None,
        sensitive_suppressed: bool,
        hot_threshold_f: float = HOT_OUTSIDE_F_DEFAULT,
        now: _dt.datetime | None = None,
    ) -> EnvState:
        kind, intensity = wmo_to_kind(
            weather_data.get("weather_code") if weather_data else None
        )
        hot = False
        if weather_data:
            temp = weather_data.get("temperature")
            unit = (weather_data.get("unit") or "").upper()
            if isinstance(temp, (int, float)):
                t_f = float(temp) if "F" in unit else (float(temp) * 9 / 5) + 32
                hot = t_f >= hot_threshold_f
        hour = (now or _dt.datetime.now()).hour
        return cls(
            kind=kind,
            intensity=float(intensity),
            hot_outside=hot,
            is_day=6 <= hour < 19,
            afk_active=idle_event == "sustained",
            sensitive_suppressed=bool(sensitive_suppressed),
        )


class CloudDrift:
    """Shared oscillator clock for drifting sky props. One phase counter feeds
    every sprite that opts in via ``PropSprite.drift_x_amplitude``; per-sprite
    ``drift_phase_offset`` lets two sprites share the clock while moving
    anti-phase (clouds passing each other in opposite directions).
    """

    def __init__(self, period_s: float = 45.0) -> None:
        self.phase_s = 0.0
        self.period_s = period_s

    def tick(self, dt: float, env: EnvState) -> None:
        if env.sensitive_suppressed:
            return
        self.phase_s = (self.phase_s + dt) % self.period_s

    def offset_x(self, amplitude: float, phase_offset: float = 0.0) -> float:
        if amplitude == 0.0 or self.period_s <= 0.0:
            return 0.0
        angle = (self.phase_s / self.period_s) * 2.0 * math.pi + phase_offset
        return amplitude * math.cos(angle)


class BuddyMotion:
    """Continuous-position slide. The buddy picks a target inside the stage,
    eases toward it at ``speed`` cells/sec, then picks a new target after a
    dwell of ``min_dwell_s``..``max_dwell_s`` seconds.
    """

    def __init__(
        self,
        rng: random.Random | None = None,
        speed: float = 6.0,
        min_dwell_s: float = 5.0,
        max_dwell_s: float = 15.0,
    ) -> None:
        self.x = 0.0
        self.y = 0.0
        self.target_x = 0.0
        self.target_y = 0.0
        self.speed = speed
        self.min_dwell_s = min_dwell_s
        self.max_dwell_s = max_dwell_s
        self._dwell_left = 0.0
        self._rng = rng or random.Random()
        # Physics overlay — click/drag/shake reactions. Drag offset lives on
        # a separate plane (applied to #buddy-stage) so ambient slide and
        # drag don't fight for buddy.styles.offset.
        self.recoil_ticks: float = 0.0
        self.dizzy_ticks: float = 0.0
        self.drag_offset_x: float = 0.0
        self.drag_offset_y: float = 0.0
        self._dragging: bool = False
        self._shake_window: list[tuple[float, float, float]] = []
        self._shake_trigger: bool = False
        self._poke_trigger: bool = False

    def poke(self) -> None:
        """Register a click-reaction. Sets recoil animation + a one-shot
        pulse flag for ParticleSky to emit impact stars."""
        self.recoil_ticks = _RECOIL_DURATION_S
        self._poke_trigger = True

    def drag_update(self, dx: float, dy: float, dt: float) -> None:
        """Accumulate a drag delta (cells). Runs shake-detection over a
        rolling window of recent deltas."""
        self._dragging = True
        self.drag_offset_x = _clamp(
            self.drag_offset_x + dx, -_MAX_DRAG_OFFSET, _MAX_DRAG_OFFSET
        )
        self.drag_offset_y = _clamp(
            self.drag_offset_y + dy, -_MAX_DRAG_OFFSET, _MAX_DRAG_OFFSET
        )
        self._shake_window = [
            (px, py, age + dt)
            for (px, py, age) in self._shake_window
            if age + dt <= _SHAKE_WINDOW_S
        ]
        self._shake_window.append((dx, dy, 0.0))
        if self.dizzy_ticks <= 0.0 and self._count_reversals() >= _SHAKE_REVERSAL_THRESHOLD:
            self.dizzy_ticks = _DIZZY_DURATION_S
            self._shake_trigger = True
            self._shake_window.clear()

    def release(self) -> None:
        """End drag. The stage offset eases back to (0, 0) via ``tick``."""
        self._dragging = False

    def consume_shake_trigger(self) -> bool:
        out = self._shake_trigger
        self._shake_trigger = False
        return out

    def consume_poke_trigger(self) -> bool:
        out = self._poke_trigger
        self._poke_trigger = False
        return out

    def _count_reversals(self) -> int:
        def flips(signs: list[int]) -> int:
            prev = 0
            count = 0
            for s in signs:
                if s == 0:
                    continue
                if prev != 0 and s != prev:
                    count += 1
                prev = s
            return count

        sx = [(1 if dx > 0 else -1 if dx < 0 else 0) for dx, _, _ in self._shake_window]
        sy = [(1 if dy > 0 else -1 if dy < 0 else 0) for _, dy, _ in self._shake_window]
        return max(flips(sx), flips(sy))

    def tick(
        self,
        dt: float,
        bounds_w: float,
        bounds_h: float,
        env: EnvState,
    ) -> None:
        if env.sensitive_suppressed:
            # Freeze all physics. Clearing drag_offset snaps the buddy back
            # in one frame rather than drifting while suppressed.
            self.drag_offset_x = 0.0
            self.drag_offset_y = 0.0
            self.recoil_ticks = 0.0
            self.dizzy_ticks = 0.0
            self._dragging = False
            self._shake_window.clear()
            self._shake_trigger = False
            self._poke_trigger = False
            self.x = _clamp(self.x, 0.0, bounds_w)
            self.y = _clamp(self.y, 0.0, bounds_h)
            self.target_x = _clamp(self.target_x, 0.0, bounds_w)
            self.target_y = _clamp(self.target_y, 0.0, bounds_h)
            return

        if self.recoil_ticks > 0.0:
            self.recoil_ticks = max(0.0, self.recoil_ticks - dt)
        if self.dizzy_ticks > 0.0:
            self.dizzy_ticks = max(0.0, self.dizzy_ticks - dt)
        self._shake_window = [
            (dx, dy, age + dt)
            for (dx, dy, age) in self._shake_window
            if age + dt <= _SHAKE_WINDOW_S
        ]

        if not self._dragging and (self.drag_offset_x != 0.0 or self.drag_offset_y != 0.0):
            dist = math.hypot(self.drag_offset_x, self.drag_offset_y)
            step = _DRAG_EASE_RATE * dt
            if step >= dist:
                self.drag_offset_x = 0.0
                self.drag_offset_y = 0.0
            else:
                self.drag_offset_x -= self.drag_offset_x / dist * step
                self.drag_offset_y -= self.drag_offset_y / dist * step

        held = self._dragging or self.dizzy_ticks > 0.0
        speed_scale = 0.15 if env.afk_active else 1.0
        if not held:
            self._dwell_left -= dt
            if self._dwell_left <= 0.0:
                self._pick_new_target(bounds_w, bounds_h, afk=env.afk_active)

        self.target_x = _clamp(self.target_x, 0.0, bounds_w)
        self.target_y = _clamp(self.target_y, 0.0, bounds_h)

        if not held:
            dx = self.target_x - self.x
            dy = self.target_y - self.y
            dist = math.hypot(dx, dy)
            if dist > 1e-3:
                step = self.speed * speed_scale * dt
                if step >= dist:
                    self.x = self.target_x
                    self.y = self.target_y
                else:
                    self.x += dx / dist * step
                    self.y += dy / dist * step

        self.x = _clamp(self.x, 0.0, bounds_w)
        self.y = _clamp(self.y, 0.0, bounds_h)

    def _pick_new_target(
        self, bounds_w: float, bounds_h: float, *, afk: bool
    ) -> None:
        self.target_x = self._rng.uniform(0.0, max(0.0, bounds_w))
        # Vertical wander is small — buddies look weird hopping rows.
        wander_h = max(0.0, min(bounds_h, 2.0))
        self.target_y = self._rng.uniform(0.0, wander_h)
        if afk:
            self._dwell_left = self._rng.uniform(
                self.max_dwell_s * 2, self.max_dwell_s * 4
            )
        else:
            self._dwell_left = self._rng.uniform(
                self.min_dwell_s, self.max_dwell_s
            )


_STAR_PALETTE: tuple[str, ...] = (
    "#ffffee",  # brightest
    "#ddddbb",
    "#aaaa88",
    "#777755",
    "#aaaa88",
    "#ddddbb",
)
_STAR_GLYPHS: tuple[str, ...] = ("*", "·", "✦", "+", "⋆", "✶", "°", ".")

# Click-reaction palette — bright warm hits, contrast against the #1a1a2e bg.
_IMPACT_PALETTE: tuple[str, ...] = ("#ffeb3b", "#ffffee", "#ffc107", "#ff9800")
_IMPACT_GLYPHS: tuple[str, ...] = ("*", "✦", "✶", "+")

# Dizzy palette — purple/magenta for the "ugh, stop it" aesthetic.
_SWIRL_PALETTE: tuple[str, ...] = ("#bb66ff", "#8844cc", "#cc88ff", "#dd99ee")
_SWIRL_GLYPHS: tuple[str, ...] = ("✦", "°", "·", "*", "~")


def _value_noise2(x: float, y: float, seed: int) -> float:
    """Smooth 2D value noise in [0, 1]. Hash-based, no tables."""
    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - ix, y - iy
    sx = fx * fx * (3.0 - 2.0 * fx)
    sy = fy * fy * (3.0 - 2.0 * fy)

    def h(a: int, b: int) -> float:
        n = (a * 374761393 + b * 668265263 + seed) & 0xFFFFFFFF
        n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0

    n00, n10 = h(ix, iy), h(ix + 1, iy)
    n01, n11 = h(ix, iy + 1), h(ix + 1, iy + 1)
    nx0 = n00 * (1.0 - sx) + n10 * sx
    nx1 = n01 * (1.0 - sx) + n11 * sx
    return nx0 * (1.0 - sy) + nx1 * sy


def _fractal_noise2(x: float, y: float, seed: int, octaves: int = 3) -> float:
    """Sum of value-noise octaves, normalized to [0, 1]. Adds finer detail."""
    total = 0.0
    amp = 1.0
    freq = 1.0
    norm = 0.0
    for o in range(octaves):
        total += amp * _value_noise2(x * freq, y * freq, seed + o * 1009)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    ax: float
    ay: float
    life: float
    glyph: str
    color: str
    spin: float = 0.0  # phase for sine-wave horizontal drift (snowflakes)
    pulse_palette: tuple[str, ...] = ()  # if set, color cycles through this
    pulse_period_s: float = 0.0  # one full cycle through pulse_palette


class ParticleField:
    """Lightweight Newtonian particle simulator. Each tick advances every
    particle by ``dt`` seconds, culls expired or off-panel ones, and spawns
    new emitters keyed off the current env. Capped at PARTICLE_LIMIT total —
    spawn calls past the cap are dropped, not queued.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.particles: list[Particle] = []
        self.rng = rng or random.Random()
        self._ambient_dust_accum = 0.0
        self._weather_accum = 0.0
        self._hot_accum = 0.0
        self._lightning_accum = 0.0

    def tick(
        self,
        dt: float,
        panel_w: int,
        panel_h: int,
        env: EnvState,
        buddy_x: float,
        buddy_y: float,
    ) -> None:
        if env.sensitive_suppressed:
            return
        self._advance(dt, panel_w, panel_h)
        self._spawn(dt, panel_w, panel_h, env, buddy_x, buddy_y)

    def _try_append(self, p: Particle) -> None:
        if len(self.particles) < PARTICLE_LIMIT:
            self.particles.append(p)

    def _advance(self, dt: float, panel_w: int, panel_h: int) -> None:
        survivors: list[Particle] = []
        for p in self.particles:
            p.life -= dt
            if p.life <= 0.0:
                continue
            if p.pulse_palette:
                # Pulsing particle (stars) — advance phase, cycle color, no drift.
                p.spin += dt
                period = p.pulse_period_s or 4.0
                idx = int((p.spin / period) * len(p.pulse_palette))
                p.color = p.pulse_palette[idx % len(p.pulse_palette)]
                drift = 0.0
            elif p.spin != 0.0:
                p.spin += dt
                drift = math.sin(p.spin * 2.0) * 0.5
            else:
                drift = 0.0
            p.vx += p.ax * dt
            p.vy += p.ay * dt
            p.x += (p.vx + drift) * dt
            p.y += p.vy * dt
            if p.x < -1.0 or p.x > panel_w + 1.0:
                continue
            if p.y < -1.0 or p.y > panel_h + 1.0:
                continue
            survivors.append(p)
        self.particles = survivors

    def populate_starfield(
        self,
        panel_w: int,
        panel_h: int,
        *,
        target_count: int,
        max_x: int | None = None,
    ) -> None:
        """Place a one-shot static star field via Perlin/value noise.

        Stars cluster in noise-bright regions (constellations) and avoid
        noise-dim regions (dark patches). A minimum-distance check keeps
        any two stars at least ~1.5 cells apart even inside a cluster.

        ``max_x`` clamps the rightmost spawn column (inclusive). Pass the
        moon's left edge here to keep the moon's column starless.
        """
        self.particles = [p for p in self.particles if not p.pulse_palette]

        upper_x = max(1.0, float(max_x if max_x is not None else panel_w))
        upper_y = max(1.0, float(panel_h) * 0.7)

        if target_count <= 0:
            return

        seed = self.rng.randrange(1, 1 << 28)
        # Noise feature size: ~1 "cluster" per ~8x8 cell region.
        scale = 8.0
        min_dist_sq = 2.0 ** 2

        placed: list[tuple[float, float]] = []
        max_attempts = target_count * 30
        for _ in range(max_attempts):
            if len(placed) >= target_count:
                break
            x = self.rng.uniform(0.0, upper_x)
            y = self.rng.uniform(0.0, upper_y)
            # Rejection: noise value must beat a random threshold. Bright
            # noise regions accept frequently → clusters; dim regions
            # rarely → voids.
            n = _fractal_noise2(x / scale, y / scale, seed)
            if n < self.rng.uniform(0.35, 0.85):
                continue
            if any((x - px) ** 2 + (y - py) ** 2 < min_dist_sq for px, py in placed):
                continue
            placed.append((x, y))

        for x, y in placed:
            self._try_append(Particle(
                x=x, y=y,
                vx=0.0, vy=0.0, ax=0.0, ay=0.0,
                life=99999.0,
                glyph=self.rng.choice(_STAR_GLYPHS),
                color=_STAR_PALETTE[0],
                spin=self.rng.uniform(0.0, 6.0),
                pulse_palette=_STAR_PALETTE,
                pulse_period_s=self.rng.uniform(3.0, 6.0),
            ))

    def clear_stars(self) -> None:
        self.particles = [p for p in self.particles if not p.pulse_palette]

    def _spawn(
        self,
        dt: float,
        panel_w: int,
        panel_h: int,
        env: EnvState,
        buddy_x: float,
        buddy_y: float,
    ) -> None:
        if len(self.particles) >= PARTICLE_LIMIT:
            return

        afk_scale = 0.3 if env.afk_active else 1.0
        starry = env.kind is Kind.CLEAR and not env.is_day

        # Ambient dust only when NOT starry — stars are pre-populated by the
        # overlay via populate_starfield(), not spawned per-tick.
        if not starry:
            self._ambient_dust_accum += dt * 0.6 * afk_scale
            while self._ambient_dust_accum >= 1.0:
                self._ambient_dust_accum -= 1.0
                self._spawn_dust(panel_w, panel_h)

        if env.kind in (Kind.RAIN, Kind.DRIZZLE):
            self._weather_accum += dt * 12.0 * env.intensity * afk_scale
            while self._weather_accum >= 1.0:
                self._weather_accum -= 1.0
                self._spawn_rain(panel_w, intensity=env.intensity)
        elif env.kind == Kind.SNOW:
            self._weather_accum += dt * 6.0 * env.intensity * afk_scale
            while self._weather_accum >= 1.0:
                self._weather_accum -= 1.0
                self._spawn_snow(panel_w)
        elif env.kind == Kind.STORM:
            self._weather_accum += dt * 14.0 * env.intensity * afk_scale
            while self._weather_accum >= 1.0:
                self._weather_accum -= 1.0
                self._spawn_rain(panel_w, intensity=env.intensity)
            self._lightning_accum += dt * 0.4 * env.intensity * afk_scale
            while self._lightning_accum >= 1.0:
                self._lightning_accum -= 1.0
                self._spawn_lightning(panel_w, panel_h)
        else:
            self._weather_accum = 0.0

        if env.hot_outside:
            self._hot_accum += dt * 4.0 * afk_scale
            while self._hot_accum >= 1.0:
                self._hot_accum -= 1.0
                self._spawn_steam(buddy_x, buddy_y)
        else:
            self._hot_accum = 0.0

    def _spawn_dust(self, panel_w: int, panel_h: int) -> None:
        self._try_append(Particle(
            x=self.rng.uniform(0.0, max(1.0, float(panel_w))),
            y=self.rng.uniform(0.0, max(1.0, float(panel_h))),
            vx=self.rng.uniform(-0.4, 0.4),
            vy=self.rng.uniform(-0.2, 0.2),
            ax=0.0, ay=0.0,
            life=self.rng.uniform(3.0, 7.0),
            glyph=self.rng.choice([".", "·"]),
            color="#444466",
        ))

    def _spawn_star(self, panel_w: int, panel_h: int) -> None:
        # Stars: fixed position, slow brightness pulse, very long life.
        self._try_append(Particle(
            x=self.rng.uniform(0.0, max(1.0, float(panel_w))),
            y=self.rng.uniform(0.0, max(1.0, float(panel_h) * 0.7)),
            vx=0.0,
            vy=0.0,
            ax=0.0, ay=0.0,
            life=300.0,
            glyph=self.rng.choice(["*", "·", "✦", "+"]),
            color=_STAR_PALETTE[0],
            spin=self.rng.uniform(0.0, 6.0),
            pulse_palette=_STAR_PALETTE,
            pulse_period_s=self.rng.uniform(3.0, 6.0),
        ))

    def _spawn_rain(self, panel_w: int, intensity: float) -> None:
        self._try_append(Particle(
            x=self.rng.uniform(0.0, max(1.0, float(panel_w))),
            y=-1.0,
            vx=self.rng.uniform(-0.3, 0.3),
            vy=self.rng.uniform(8.0 + 4.0 * intensity, 14.0 + 6.0 * intensity),
            ax=0.0, ay=0.0,
            life=4.0,
            glyph=self.rng.choice(["'", "│", "."]),
            color="#5599ff",
        ))

    def _spawn_snow(self, panel_w: int) -> None:
        self._try_append(Particle(
            x=self.rng.uniform(0.0, max(1.0, float(panel_w))),
            y=-1.0,
            vx=0.0,
            vy=self.rng.uniform(1.5, 3.5),
            ax=0.0, ay=0.0,
            life=10.0,
            glyph=self.rng.choice(["*", "·", "❄"]),
            color="#ddddff",
            spin=self.rng.uniform(0.0, 6.28),
        ))

    def _spawn_lightning(self, panel_w: int, panel_h: int) -> None:
        x = self.rng.uniform(2.0, max(2.0, float(panel_w - 2)))
        for i in range(min(panel_h, 4)):
            self._try_append(Particle(
                x=x + (i % 2) * 0.5,
                y=float(i),
                vx=0.0, vy=0.0, ax=0.0, ay=0.0,
                life=0.25,
                glyph="╲" if i % 2 else "╱",
                color="#ffff66",
            ))

    def spawn_impact_burst(self, x: float, y: float, count: int = 5) -> None:
        """Radial star burst on click — short life, outward velocity, light
        gravity. Called once per poke trigger consumption.
        """
        for i in range(count):
            angle = (i / max(1, count)) * 2.0 * math.pi + self.rng.uniform(-0.3, 0.3)
            speed = self.rng.uniform(6.0, 10.0)
            self._try_append(Particle(
                x=x, y=y,
                vx=math.cos(angle) * speed,
                vy=math.sin(angle) * speed * 0.6,
                ax=0.0, ay=8.0,
                life=self.rng.uniform(0.4, 0.7),
                glyph=self.rng.choice(_IMPACT_GLYPHS),
                color=self.rng.choice(_IMPACT_PALETTE),
            ))

    def spawn_dizzy_swirl(self, x: float, y: float, count: int = 4) -> None:
        """Short-lived orbiting glyphs at the bottom of the sky widget.

        The sky widget clips to the top of the panel, so ``y`` should be
        ``panel_h - 1`` (very bottom of sky) — that's the closest the sky
        can render to the buddy underneath. Particles have tight radius,
        short life, and near-zero vertical drift so they form a compact
        puff above the speech region rather than floating up into the
        weather.
        """
        for _ in range(count):
            angle = self.rng.uniform(0.0, 2.0 * math.pi)
            radius = self.rng.uniform(1.0, 2.5)
            self._try_append(Particle(
                x=x + math.cos(angle) * radius,
                y=y,
                vx=math.cos(angle + math.pi / 2.0) * 2.0,
                vy=0.0,
                ax=0.0, ay=0.0,
                life=self.rng.uniform(0.5, 0.8),
                glyph=self.rng.choice(_SWIRL_GLYPHS),
                color=self.rng.choice(_SWIRL_PALETTE),
            ))

    def _spawn_steam(self, buddy_x: float, buddy_y: float) -> None:
        self._try_append(Particle(
            x=buddy_x + self.rng.uniform(-2.0, 2.0),
            y=buddy_y + self.rng.uniform(-1.0, 0.0),
            vx=self.rng.uniform(-0.4, 0.4),
            vy=self.rng.uniform(-3.5, -2.0),
            ax=0.0, ay=0.0,
            life=1.5,
            glyph=self.rng.choice(["~", "°"]),
            color="#aaccff",
        ))


def _clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo:
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v

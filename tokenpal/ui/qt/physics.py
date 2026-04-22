"""Spring-pendulum integrator for the dangleable Qt buddy.

Pure Python, no Qt imports. The Qt side owns the 60 Hz QTimer and the
mouse events; this module just takes ``tick(dt)`` calls and anchor
updates, and returns the buddy's current (x, y).

See plans/new-ui-new-me.md §"Dangle-able" v1 for the model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PhysicsConfig:
    spring_k: float = 180.0          # px/s² per px of displacement (Hooke-ish)
    gravity: float = 1200.0          # px/s² downward
    damping: float = 12.0            # velocity-proportional damping (ζ≈0.45)
    mass: float = 1.0                # acceleration = F/m
    max_speed: float = 2000.0        # clamp per axis so flicks stay on-screen
    settle_speed: float = 1.0        # px/s — "effectively stopped"
    settle_distance: float = 0.5     # px from rest equilibrium
    settle_ticks_required: int = 10  # consecutive ticks below thresholds


@dataclass
class _State:
    pos_x: float
    pos_y: float
    vel_x: float = 0.0
    vel_y: float = 0.0


class DangleSimulator:
    """Semi-implicit-Euler spring-pendulum between anchor and body.

    At rest, gravity pulls the body below the anchor by ``g * m / k``.
    The simulator auto-sleeps once the body has stayed within
    ``settle_distance`` of rest at ``settle_speed`` for
    ``settle_ticks_required`` ticks. Any anchor move or external impulse
    wakes it.
    """

    def __init__(
        self,
        anchor: tuple[float, float],
        initial_pos: tuple[float, float] | None = None,
        config: PhysicsConfig | None = None,
    ) -> None:
        self._config = config or PhysicsConfig()
        self._anchor = anchor
        start = initial_pos if initial_pos is not None else self.rest_position()
        self._state = _State(pos_x=start[0], pos_y=start[1])
        self._settled_ticks = 0
        self._sleeping = False

    @property
    def config(self) -> PhysicsConfig:
        return self._config

    @property
    def position(self) -> tuple[float, float]:
        return (self._state.pos_x, self._state.pos_y)

    @property
    def velocity(self) -> tuple[float, float]:
        return (self._state.vel_x, self._state.vel_y)

    @property
    def anchor(self) -> tuple[float, float]:
        return self._anchor

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    def rest_position(self) -> tuple[float, float]:
        """Equilibrium point: anchor plus gravitational droop."""
        cfg = self._config
        droop = cfg.gravity * cfg.mass / cfg.spring_k
        return (self._anchor[0], self._anchor[1] + droop)

    def set_anchor(self, x: float, y: float) -> None:
        self._anchor = (x, y)
        self._wake()

    def apply_impulse(self, vx: float, vy: float) -> None:
        """Add to current velocity (e.g. fling on mouse release)."""
        self._state.vel_x += vx
        self._state.vel_y += vy
        self._clamp_velocity()
        self._wake()

    def tick(self, dt: float) -> tuple[float, float]:
        """Advance one step. Returns current (x, y). No-op if sleeping."""
        if self._sleeping or dt <= 0:
            return self.position

        cfg = self._config
        s = self._state
        ax = (-cfg.spring_k * (s.pos_x - self._anchor[0])
              - cfg.damping * s.vel_x) / cfg.mass
        ay = ((-cfg.spring_k * (s.pos_y - self._anchor[1])
               - cfg.damping * s.vel_y) / cfg.mass
              + cfg.gravity)

        # Semi-implicit Euler: update velocity first, then use new velocity
        # for position. More stable than explicit Euler for stiff springs.
        s.vel_x += ax * dt
        s.vel_y += ay * dt
        self._clamp_velocity()
        s.pos_x += s.vel_x * dt
        s.pos_y += s.vel_y * dt

        self._check_settle()
        return self.position

    def _wake(self) -> None:
        self._sleeping = False
        self._settled_ticks = 0

    def _clamp_velocity(self) -> None:
        cap = self._config.max_speed
        s = self._state
        if s.vel_x > cap:
            s.vel_x = cap
        elif s.vel_x < -cap:
            s.vel_x = -cap
        if s.vel_y > cap:
            s.vel_y = cap
        elif s.vel_y < -cap:
            s.vel_y = -cap

    def _check_settle(self) -> None:
        cfg = self._config
        rest_x, rest_y = self.rest_position()
        s = self._state
        speed = math.hypot(s.vel_x, s.vel_y)
        dist = math.hypot(s.pos_x - rest_x, s.pos_y - rest_y)
        if speed < cfg.settle_speed and dist < cfg.settle_distance:
            self._settled_ticks += 1
            if self._settled_ticks >= cfg.settle_ticks_required:
                # Snap to rest so floating-point drift doesn't keep the
                # buddy half a pixel off forever.
                s.pos_x = rest_x
                s.pos_y = rest_y
                s.vel_x = 0.0
                s.vel_y = 0.0
                self._sleeping = True
        else:
            self._settled_ticks = 0


def run_until_settled(
    sim: DangleSimulator, dt: float = 1.0 / 60.0, max_ticks: int = 600,
) -> int:
    """Tick until the simulator sleeps. Returns the number of ticks it
    took. Raises RuntimeError if it never settles within the budget."""
    for i in range(max_ticks):
        sim.tick(dt)
        if sim.sleeping:
            return i + 1
    raise RuntimeError(
        f"DangleSimulator did not settle within {max_ticks} ticks "
        f"({max_ticks * dt:.2f}s)",
    )


DEFAULT_CONFIG = PhysicsConfig()

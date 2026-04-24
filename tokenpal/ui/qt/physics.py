"""Spring-pendulum integrator for the dangleable Qt buddy.

Pure Python, no Qt imports. The Qt side owns the 60 Hz QTimer and the
mouse events; this module just takes ``tick(dt)`` calls and anchor
updates, and returns the buddy's current (x, y).

See plans/new-ui-new-me.md §"Dangle-able" v1 for the model.
"""

from __future__ import annotations

import math
from collections import deque
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
    sim: DangleSimulator | PendulumSimulator,
    dt: float = 1.0 / 60.0,
    max_ticks: int = 600,
) -> int:
    """Tick until the simulator sleeps. Returns the number of ticks it
    took. Raises RuntimeError if it never settles within the budget."""
    for i in range(max_ticks):
        sim.tick(dt)
        if sim.sleeping:
            return i + 1
    raise RuntimeError(
        f"simulator did not settle within {max_ticks} ticks "
        f"({max_ticks * dt:.2f}s)",
    )


DEFAULT_CONFIG = PhysicsConfig()


# ---------------------------------------------------------------------------
# PendulumSimulator — rigid pendulum pivoted at an externally-driven point.
# Replaces DangleSimulator for the swing-it model: the pivot follows the
# cursor rigidly (no translational spring) and the body rotates around it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendulumConfig:
    gravity: float = 2000.0          # px/s² — strength of restoring torque
    damping: float = 2.5              # intrinsic angular damping at rest
    # (1/s). See ``spin_damping_floor`` — this value is attenuated when
    # the body is already spinning fast, so the user can pump energy
    # into the swing via circular cursor motion (driven-pendulum
    # resonance) without light-damping's "never settles" downside.
    # Fraction of total damping (intrinsic + drag-induced) that remains
    # at |ω| = max_angular_speed. At rest full damping applies (buddy
    # settles cleanly); at the angular speed cap only this fraction
    # remains, so a sustained twirl keeps going.
    spin_damping_floor: float = 0.02
    # Additive torque (rad/s² per rad/s of cursor circling) that drives
    # body rotation in the cursor's detected circular direction. Pure
    # drive — NOT a PID on (ω_cursor − ω_body). PID felt wrong because
    # it brakes body's existing momentum at the top of the orbit (where
    # body may momentarily spin opposite cursor while passing through),
    # producing an "invisible pillow" stall. Pure drive only accelerates
    # in cursor's direction; damping provides the ceiling. 0 disables.
    #
    # Tuned high enough that the steady-state ω solution from
    # (damping + drag/mass)·damp_factor·ω == coupling·ω_cursor has no
    # real root below max_angular_speed — body saturates at cap for
    # ω_cursor ≳ 0.5 rad/s. Keeps the orbit pinned to the cap so
    # momentary cursor-velocity dips (natural at a hand-drawn circle's
    # turning points) don't drop body ω and trigger gravity kick-ins.
    circular_coupling: float = 12.0
    # Deque depth for cursor-history-based circular-rate estimation.
    # At 60 Hz, 24 samples = 0.4 s window — long enough to see a
    # ~2 rad/s circle (46° sweep), short enough to respond quickly.
    circular_history_depth: int = 24
    # EMA weight on the cursor-rate signal feeding the coupling drive.
    # Real hand-drawn circles aren't perfectly uniform — ω_cursor
    # jitters as the user slows at turning points, wobbles, or varies
    # speed. Without smoothing, coupling torque dips with every jitter
    # and body ω sags below cap (reads as a "reversal" feel during
    # orbit). 0.08 ≈ ~0.8 s time constant — survives hand jitter,
    # still responds to actual stop/start within a second.
    circular_rate_smoothing: float = 0.08
    # Wind-drag coefficient (1/s). Creates velocity-proportional forcing:
    # while the cursor keeps moving the body keeps trailing behind, even
    # at constant drag speed. An acceleration-only model felt wrong on
    # continuous drags and reversals (body drifted to vertical whenever
    # accel went to zero). Non-zero drag gives steady-state tilt
    # θ_ss ≈ -drag · v_pivot / (gravity · mass) at small angles.
    drag: float = 9.0
    # Acceleration-based pseudo-force ("yank"). When the cursor
    # accelerates, the body's inertia in the accelerating frame
    # produces a tangential torque. This gives an *impulsive* kick per
    # rapid cursor direction change, on top of the steady wind-drag
    # forcing — crucial for breaking out of the "oscillates but won't
    # flip" regime that hand-drawn-speed cursor circles fall into.
    yank: float = 2.5
    # Perceived "weight" knob. In a real rigid pendulum mass cancels out
    # of θ''; we use it here only to scale the pivot-velocity forcing
    # (higher ⇒ buddy drags more sluggishly without changing the natural
    # oscillation period). Not physically accurate, but it's a useful
    # feel dial.
    mass: float = 1.8
    # EMA weight on pivot velocity before it's used for forcing. Raw
    # per-tick finite-diff velocity alternates between huge and zero
    # (mouse events don't land on tick boundaries); smoothing keeps the
    # forcing term coherent. 0.0 freezes velocity; 1.0 disables smooth.
    pivot_vel_smoothing: float = 0.25
    # Fling impulse scale: tangential cursor velocity / L gives the raw
    # angular impulse; unscaled it's usually enough to spin the buddy
    # several full revolutions, which reads as broken. 0.35 keeps the
    # impulse punchy without going full washing-machine.
    fling_scale: float = 0.35
    # Angular-velocity scale at which wind-drag and yank fade out.
    # Above this body rotation rate, the cursor's absolute velocity and
    # acceleration stop contributing to torque — they're parasitic
    # during orbit, producing torque that oscillates at 2·ω_body and
    # gets amplified by large-circle cursor motions (|v| = R·ω_c,
    # |a| = R·ω_c²). Wind-drag and yank remain fully active near rest
    # so small drags and whip motions still feel responsive.
    spin_lockout_rate: float = 4.0
    # 25 rad/s (~4 revolutions/s). Sustained orbit requires θ_dot² > 4g/L
    # (~8.9 rad/s for default art); the cap leaves ~3x headroom so the
    # body doesn't constantly bump against it during pumping.
    max_angular_speed: float = 25.0
    settle_speed: float = 0.1         # rad/s — "effectively stopped"
    settle_angle: float = 0.05        # rad from rest (~2.9°)
    settle_ticks_required: int = 15
    # px floor on the pendulum length. Beyond the division-by-zero
    # safety role, this normalizes feel across grab points: natural
    # pendulum stiffness scales as g/L, so a head-grab (L≈30 on a
    # 100px-tall buddy) would be 2.3× twitchier than a foot-grab (L≈70)
    # without clamping. 45 pulls the ratio from 2.3 down to ~1.6 so
    # head and foot grabs feel close but not identical.
    min_length: float = 45.0


class PendulumSimulator:
    """Rigid pendulum with an externally-driven pivot.

    State is ``theta`` (rad, signed so that rendering ``painter.rotate(deg)``
    matches: positive ``theta`` swings the feet to the LEFT in screen
    coords, negative swings them right) and ``theta_dot``. The pivot is
    driven externally via :meth:`set_pivot`; the simulator EMA-smooths
    pivot velocity and feeds it into a wind-drag EOM::

        θ'' = -g·sinθ/L + drag·(vₓ·cosθ - vᵧ·sinθ)/(L·m)
              - (c + drag/m)·θ'

    Small-angle steady state with ``vₓ > 0`` (cursor moving right) is
    ``θ ≈ drag·vₓ / (g·m)`` — positive, i.e. feet trail left. Body holds
    that tilt as long as the cursor keeps moving; the acceleration-only
    model it replaced let the body drift back to vertical during a
    constant-velocity drag.

    Auto-sleeps when ``|θ|``, ``|θ'|``, and pivot velocity all stay under
    their thresholds for ``settle_ticks_required`` consecutive ticks.
    """

    def __init__(
        self,
        pivot: tuple[float, float],
        length: float,
        config: PendulumConfig | None = None,
    ) -> None:
        self._cfg = config or PendulumConfig()
        self._pivot = pivot
        self._length = max(length, self._cfg.min_length)
        self._theta = 0.0
        self._theta_dot = 0.0
        self._pivot_prev = pivot
        self._pivot_vel = (0.0, 0.0)  # EMA-smoothed; drives wind-drag
        self._pivot_accel = (0.0, 0.0)  # EMA-smoothed; drives yank
        self._omega_cursor_smoothed = 0.0  # EMA-smoothed; drives coupling
        # Cursor position history for circular-rate estimation, stored
        # as (sim_time, x, y). See _cursor_angular_rate().
        self._sim_time = 0.0
        self._cursor_history: deque[tuple[float, float, float]] = deque(
            maxlen=self._cfg.circular_history_depth,
        )
        self._cursor_history.append((0.0, pivot[0], pivot[1]))
        self._settled_ticks = 0
        self._sleeping = False

    @property
    def pivot(self) -> tuple[float, float]:
        return self._pivot

    @property
    def pivot_vel(self) -> tuple[float, float]:
        """EMA-smoothed pivot velocity from the most recent tick. Exposed
        for debug HUDs; not needed for normal operation."""
        return self._pivot_vel

    @property
    def theta(self) -> float:
        return self._theta

    @property
    def theta_dot(self) -> float:
        return self._theta_dot

    @property
    def length(self) -> float:
        return self._length

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    @property
    def config(self) -> PendulumConfig:
        return self._cfg

    def set_pivot(self, x: float, y: float) -> None:
        if (x, y) == self._pivot:
            return
        self._pivot = (x, y)
        self._wake()

    def snap_pivot(self, x: float, y: float) -> None:
        """Teleport the pivot without registering the jump as motion.

        Used on mouse-press when the grab point changes — we don't want
        the finite-diff velocity estimator to treat the discontinuous
        pivot change as a blindingly fast drag and yank the body.
        """
        self._pivot = (x, y)
        self._pivot_prev = (x, y)
        self._pivot_vel = (0.0, 0.0)
        self._pivot_accel = (0.0, 0.0)
        self._omega_cursor_smoothed = 0.0
        # Reset circular-motion history too — otherwise stale samples
        # from the previous grab location bias the estimate.
        self._cursor_history.clear()
        self._cursor_history.append((self._sim_time, x, y))
        self._wake()

    def set_length(self, length: float) -> None:
        self._length = max(length, self._cfg.min_length)
        self._wake()

    def apply_angular_impulse(self, dtheta_dot: float) -> None:
        """Add to current angular velocity (e.g. fling on release)."""
        self._theta_dot += dtheta_dot
        self._clamp_angular_speed()
        self._wake()

    def reset_angle(self, theta: float = 0.0, theta_dot: float = 0.0) -> None:
        self._theta = theta
        self._theta_dot = theta_dot
        if theta == 0.0 and theta_dot == 0.0:
            return
        self._wake()

    def tick(self, dt: float) -> float:
        """Advance one step. Returns current ``theta``. No-op if sleeping."""
        if self._sleeping or dt <= 0:
            return self._theta
        cfg = self._cfg
        self._sim_time += dt
        self._cursor_history.append(
            (self._sim_time, self._pivot[0], self._pivot[1]),
        )

        # Pivot velocity: finite diff → EMA smooth. Raw per-tick velocity
        # jitters when mouse events don't align with tick boundaries.
        inst_vx = (self._pivot[0] - self._pivot_prev[0]) / dt
        inst_vy = (self._pivot[1] - self._pivot_prev[1]) / dt
        self._pivot_prev = self._pivot
        alpha = cfg.pivot_vel_smoothing
        prev_vx, prev_vy = self._pivot_vel
        vx = alpha * inst_vx + (1.0 - alpha) * prev_vx
        vy = alpha * inst_vy + (1.0 - alpha) * prev_vy
        self._pivot_vel = (vx, vy)
        # Pivot acceleration: one more finite-diff, same EMA filter.
        # Used by the yank term to give impulsive forcing on rapid
        # cursor direction changes (lets a whip move over-the-top
        # without requiring cursor speeds the user can't physically
        # sustain).
        inst_ax = (vx - prev_vx) / dt
        inst_ay = (vy - prev_vy) / dt
        ax = alpha * inst_ax + (1.0 - alpha) * self._pivot_accel[0]
        ay = alpha * inst_ay + (1.0 - alpha) * self._pivot_accel[1]
        self._pivot_accel = (ax, ay)

        sin_t = math.sin(self._theta)
        cos_t = math.cos(self._theta)
        mass = max(cfg.mass, 1e-3)
        # Velocity-based wind-drag forcing: while the cursor is moving
        # right (vx > 0), the tangential drag component at θ=0 drives
        # θ_ddot negative (body swings left). This is what makes a
        # constant-velocity drag hold the body at a steady tilt —
        # acceleration-only forcing let it drift back to vertical.
        # Drag also adds to the damping because a rotating body slices
        # through the same imaginary wind.
        drag = cfg.drag
        # Spin-dependent damping: full at rest (clean settle), attenuated
        # to `spin_damping_floor` fraction once |ω| reaches the cap, so
        # a sustained twirl can actually accumulate energy. Applied to
        # both the intrinsic damping and the wind-drag damping — at
        # near-orbit speeds the cursor-free friction has to back off or
        # resonant pumping can't keep up with dissipation.
        spin_ratio = min(
            abs(self._theta_dot) / cfg.max_angular_speed, 1.0,
        )
        damp_factor = 1.0 - (1.0 - cfg.spin_damping_floor) * spin_ratio
        # Sign on the forcing term is positive: in Qt's coordinate system
        # painter.rotate(+deg) rotates CW in screen, which swings the
        # FEET to the LEFT for a top-pivot. So a rightward cursor
        # (vx > 0) needs to push θ positive to make the feet trail left,
        # matching the user's "drag right → feet go left" intuition.
        # Cursor's detected circular-motion rate drives body ω as a
        # pure additive torque — no PID feedback on body's current ω.
        # EMA-smoothed so hand-jitter on an imperfect circle doesn't
        # pulse the drive and sag body ω below cap.
        omega_cursor_raw = self._cursor_angular_rate()
        rate_alpha = cfg.circular_rate_smoothing
        self._omega_cursor_smoothed = (
            rate_alpha * omega_cursor_raw
            + (1.0 - rate_alpha) * self._omega_cursor_smoothed
        )
        omega_cursor = self._omega_cursor_smoothed
        # Spin-fade: scale down position-dependent forcing as body
        # rotation grows. Full at rest, zero at the speed cap.
        #   - wind-drag and yank project cursor velocity/accel onto
        #     the body's tangent. At high ω this oscillates at 2·ω,
        #     driving parasitic back-and-forth pump (especially bad
        #     for big cursor circles with high |v|, |a|).
        #   - gravity (-g·sinθ/L) oscillates at ω during orbit: body
        #     ω sags ~10% at the top of each revolution and climbs
        #     back at the bottom (KE↔PE exchange). Reads visually as
        #     a "sine-wave" waviness in an otherwise smooth orbit.
        # Fading gravity too gives a clean constant-ω orbit once
        # driven, while leaving full gravity for settle at rest.
        spin_fade = max(
            0.0,
            1.0 - abs(self._theta_dot) / cfg.spin_lockout_rate,
        )
        theta_ddot = (
            -spin_fade * cfg.gravity * sin_t / self._length
            + spin_fade * drag * (vx * cos_t - vy * sin_t)
            / (self._length * mass)
            + spin_fade * cfg.yank * (ax * cos_t - ay * sin_t)
            / self._length
            - (cfg.damping + drag / mass) * damp_factor * self._theta_dot
            + cfg.circular_coupling * omega_cursor
        )

        # Semi-implicit Euler: velocity first, then position. Stable for
        # the stiff restoring torque at 60 Hz.
        self._theta_dot += theta_ddot * dt
        self._clamp_angular_speed()
        self._theta += self._theta_dot * dt
        # Wrap into (-π, π] so hard flings or repeated regrabs can't
        # accumulate unbounded angle — the settle check compares against
        # zero and would never fire otherwise.
        self._theta = (self._theta + math.pi) % (2.0 * math.pi) - math.pi

        self._check_settle()
        return self._theta

    def _wake(self) -> None:
        self._sleeping = False
        self._settled_ticks = 0

    def _cursor_angular_rate(self) -> float:
        """Signed angular velocity (rad/s) of the cursor around the mean
        of its recent positions. Exactly zero for straight-line and
        stationary motion; non-zero only when the path curves.

        Uses the signed "swept area" method: sum the z-component of the
        cross product r_i × r_{i+1} for successive offsets from the
        mean, normalize by the time window and the typical squared
        radius. For a perfect CW circle of radius R at rate ω, the sum
        is R²·ω·Δt_total, so the division recovers ω.

        Sign convention matches θ in this module: a CW cursor loop in
        screen coords returns *positive* because
        ``painter.rotate(+deg)`` is empirically CW in Qt's y-down
        screen, so +θ = CW too. The coupling term pulls body ω toward
        this value.
        """
        hist = self._cursor_history
        n = len(hist)
        if n < 4:
            return 0.0
        mx = sum(h[1] for h in hist) / n
        my = sum(h[2] for h in hist) / n
        total_cross = 0.0
        sum_r2 = 0.0
        for i in range(n - 1):
            rx_i, ry_i = hist[i][1] - mx, hist[i][2] - my
            rx_n, ry_n = hist[i + 1][1] - mx, hist[i + 1][2] - my
            total_cross += rx_i * ry_n - ry_i * rx_n
            sum_r2 += rx_i * rx_i + ry_i * ry_i
        dt_total = hist[-1][0] - hist[0][0]
        # Noise floor: cursor barely moving, or history span too short.
        # Without this a stationary cursor could produce sub-pixel
        # numerical noise that the coupling would amplify.
        if dt_total < 1e-3 or sum_r2 < 25.0:
            return 0.0
        avg_r2 = sum_r2 / (n - 1)
        return total_cross / (avg_r2 * dt_total)

    def _clamp_angular_speed(self) -> None:
        cap = self._cfg.max_angular_speed
        if self._theta_dot > cap:
            self._theta_dot = cap
        elif self._theta_dot < -cap:
            self._theta_dot = -cap

    def _check_settle(self) -> None:
        cfg = self._cfg
        pivot_still = math.hypot(*self._pivot_vel) < 1.0
        if (
            abs(self._theta_dot) < cfg.settle_speed
            and abs(self._theta) < cfg.settle_angle
            and pivot_still
        ):
            self._settled_ticks += 1
            if self._settled_ticks >= cfg.settle_ticks_required:
                self._theta = 0.0
                self._theta_dot = 0.0
                self._sleeping = True
        else:
            self._settled_ticks = 0


DEFAULT_PENDULUM_CONFIG = PendulumConfig()

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
    sim: DangleSimulator | PendulumSimulator | RigidBodySimulator,
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
    gravity: float = 1500.0          # px/s² — strength of restoring torque
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
    # Asymmetric tracking gain (1/s) on (ω_cursor − ω_body). Drives
    # body ω toward cursor ω so a cursor circle produces a 1:1
    # "yo-yo" lock: body's COM traces a single larger circle at
    # cursor's rate, instead of whipping multiple revolutions per
    # cursor lap. Asymmetric: torque is applied ONLY when the body is
    # under-spinning in the cursor's direction (same sign as ω_cursor
    # AND |ω_body| < |ω_cursor|), zero otherwise. That dodges the
    # "invisible pillow" failure mode of a symmetric PID, which would
    # brake the body's existing momentum at the top of an orbit
    # (where body may briefly spin opposite cursor while passing
    # through) or undo a flick the user just gave the body. Damping
    # alone bleeds off any over-shoot. 0 disables.
    #
    # Sized for ω_b → 0.95·ω_cursor at ω_cursor ≈ 2 rad/s against the
    # combined damping + drag/mass torque (~6.9 rad/s² at body
    # ω≈1.9): coupling·0.1 ≈ 6.9·1.9 ⇒ coupling ≈ 130. The previous
    # design saturated body ω to max_angular_speed for ω_cursor
    # ≳ 0.5 rad/s, which produced a camshaft pattern instead of a
    # yo-yo at hand-drawn cursor speeds.
    circular_coupling: float = 130.0
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
    # sin(θ_ss) ≈ drag · v_pivot / (gravity · mass) at small angles —
    # so drag must satisfy drag · v_max / (g · m) ≤ ~0.6 to keep the
    # body's trail angle below ~35° at hand-drawn cursor speeds
    # (~700 px/s typical). Higher values make the body whip past
    # horizontal mid-drag and then settle back, reading as an initial
    # "flip" before the trail establishes.
    drag: float = 3.0
    # Acceleration-based pseudo-force ("yank"). Originally added to
    # help break out of stuck oscillation regimes during circular
    # cursor motion, before the asymmetric tracking law made that
    # unnecessary. Kept small here (0.1) because hand-drawn drags
    # have ±5000 px/s² speed variance baked in — every variation
    # becomes a torque impulse that bounces the body around its
    # wind-drag equilibrium, reading as wobble during steady drags.
    # 0.1 leaves a faint impulsive feel on genuine direction
    # reversals without injecting noise during a constant-direction
    # drag.
    yank: float = 0.1
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
    # Cursor angular-rate scale at which gravity, wind-drag, and yank
    # fade out. Above this *cursor* circling rate, those terms stop
    # contributing — they're parasitic during orbit. Wind-drag and
    # yank produce torque that oscillates at 2·ω_body and gets
    # amplified by large-circle cursor motions (|v| = R·ω_c,
    # |a| = R·ω_c²); gravity oscillates at ω during orbit, causing
    # ~10% sag at the top of each revolution (KE↔PE exchange). The
    # gate reads `ω_cursor_smoothed`, NOT `|ω_body|`, deliberately:
    # gating on body ω would flicker as it tracks natural human
    # cursor-speed variation (1.5-3.5 rad/s on a "constant" circle),
    # making drag/yank/gravity snap on/off as the user's hand varies.
    # `ω_cursor_smoothed` has the EMA absorbing hand jitter, so the
    # gate stays stable across a steady orbit. On release ω_cursor
    # collapses to 0 immediately, restoring full gravity for clean
    # settle. Drag and yank stay fully active near rest so small
    # drags and whip motions still feel responsive.
    spin_lockout_rate: float = 2.5
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
        abs_theta_dot = abs(self._theta_dot)
        spin_ratio = min(abs_theta_dot / cfg.max_angular_speed, 1.0)
        damp_factor = 1.0 - (1.0 - cfg.spin_damping_floor) * spin_ratio
        # Sign on the forcing term is positive: in Qt's coordinate system
        # painter.rotate(+deg) rotates CW in screen, which swings the
        # FEET to the LEFT for a top-pivot. So a rightward cursor
        # (vx > 0) needs to push θ positive to make the feet trail left,
        # matching the user's "drag right → feet go left" intuition.
        # Cursor's detected circular-motion rate, EMA-smoothed so hand
        # jitter on an imperfect circle doesn't pulse the drive.
        omega_cursor_raw = self._cursor_angular_rate()
        rate_alpha = cfg.circular_rate_smoothing
        self._omega_cursor_smoothed = (
            rate_alpha * omega_cursor_raw
            + (1.0 - rate_alpha) * self._omega_cursor_smoothed
        )
        omega_cursor = self._omega_cursor_smoothed
        abs_omega_cursor = abs(omega_cursor)
        # Spin-fade: scale gravity, drag, yank DOWN as the cursor
        # sustains circular motion (full near rest, zero at
        # `spin_lockout_rate`). Tracking-gate ramps UP symmetrically.
        # Together they hand wind-drag (linear drag → trail behind)
        # and tracking (cursor circle → yo-yo lock) the same crossover
        # so they never both dominate: small incidental curl in a
        # straight drag stays in wind-drag's regime, deliberate orbit
        # is firmly in tracking's. See `spin_lockout_rate` in
        # PendulumConfig for the longer "why."
        spin_fade = max(0.0, 1.0 - abs_omega_cursor / cfg.spin_lockout_rate)
        tracking_gate = 1.0 - spin_fade
        # Asymmetric tracking torque: only accelerates in cursor's
        # direction when the body is under-spinning. Edge cases via
        # the product test: cursor=0 OR opposite signs ⇒ product ≤ 0
        # ⇒ no torque; body=0 with cursor≠0 ⇒ product = 0 ⇒ same-sign
        # branch fires (kick from rest works). Full rationale on
        # `circular_coupling` in PendulumConfig.
        if (
            omega_cursor * self._theta_dot >= 0.0
            and abs_theta_dot < abs_omega_cursor
        ):
            tracking_torque = (
                tracking_gate
                * cfg.circular_coupling
                * (omega_cursor - self._theta_dot)
            )
        else:
            tracking_torque = 0.0
        theta_ddot = (
            -spin_fade * cfg.gravity * sin_t / self._length
            + spin_fade * drag * (vx * cos_t - vy * sin_t)
            / (self._length * mass)
            + spin_fade * cfg.yank * (ax * cos_t - ay * sin_t)
            / self._length
            - (cfg.damping + drag / mass) * damp_factor * self._theta_dot
            + tracking_torque
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


# ---------------------------------------------------------------------------
# RigidBodySimulator — mouse-joint style 2D rigid body.
# Replaces PendulumSimulator for the buddy grab/drag/release loop.
# Single soft constraint at the grab anchor (Catto, Soft Constraints,
# GDC 2011) updates both linear and angular state per tick. Home spring
# pulls COM + θ back to the screen anchor when not grabbed. On release
# the constraint is dropped and the body coasts — no fling impulse,
# the velocity already lives in (vx, vy, ω).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RigidBodyConfig:
    # Soft-constraint pull on the grab anchor toward the cursor.
    # Stiff + critically damped: cursor IS the user's intent.
    grab_frequency_hz: float = 8.0
    grab_damping_ratio: float = 1.0
    # Soft spring on COM + θ pulling toward (home_x, home_y, 0).
    # Slow + critically damped: gentle return after release.
    home_frequency_hz: float = 2.0
    home_damping_ratio: float = 1.0
    # Body inertia. `inertia` is the scalar moment of inertia I; for a
    # uniform disk of radius R, I = m·R²/2. We treat the buddy art as
    # such a disk; the off-COM grab dynamics emerge from the Jacobian
    # without needing a more accurate mass distribution.
    mass: float = 1.0
    inertia: float = 4000.0
    # Velocity caps so a hard fling can't slingshot off-screen / spin
    # faster than the integrator stays stable.
    max_linear_speed: float = 4000.0
    max_angular_speed: float = 40.0
    # Settle thresholds — applied only when not grabbed.
    settle_speed: float = 1.0       # px/s
    settle_omega: float = 0.05      # rad/s
    settle_distance: float = 0.5    # px from home
    settle_angle: float = 0.02      # rad from upright (~1.1°)
    settle_ticks_required: int = 15


class RigidBodySimulator:
    """2D rigid body with a soft mouse-joint grab and a soft home spring.

    State: ``(x, y, theta)`` and velocities ``(vx, vy, omega)``. ``theta``
    follows the same Qt convention as :class:`PendulumSimulator`: positive
    swings the feet to the LEFT in screen coords (``painter.rotate(+deg)``
    is empirically CW in Qt's y-down screen).

    Without a grab, the body is pulled toward ``(home_x, home_y, 0)`` by
    a critically-damped soft spring applied directly as force + torque.

    With a grab active, a soft constraint at a body-local anchor drives
    the corresponding world-frame point toward the cursor target. The
    constraint impulse updates linear AND angular velocity in one step;
    off-COM grabs naturally rotate more than near-COM grabs because the
    parallel-axis term ``I + m·|r|²`` falls out of the effective-mass
    matrix ``K = J·M⁻¹·Jᵀ`` without explicit handling.

    On release, the constraint is dropped. The body coasts with whatever
    ``(v, ω)`` it accumulated; the home spring catches it. No separate
    fling impulse — that would double-count velocity already imparted
    via the constraint.
    """

    def __init__(
        self,
        home: tuple[float, float],
        config: RigidBodyConfig | None = None,
    ) -> None:
        self._cfg = config or RigidBodyConfig()
        self._home = home
        self._x, self._y = home
        self._theta = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._omega = 0.0
        # Grab state. ``_grab_local`` is the grab point in body-frame
        # coords (relative to COM); ``_grab_target`` is the cursor in
        # world coords. ``_grab_impulse_acc`` warm-starts the constraint
        # solver across ticks (Catto-style).
        self._grab_local: tuple[float, float] | None = None
        self._grab_target: tuple[float, float] | None = None
        self._grab_impulse_acc: tuple[float, float] = (0.0, 0.0)
        self._settled_ticks = 0
        self._sleeping = True

    # ----- read-only state -----
    @property
    def position(self) -> tuple[float, float]:
        return (self._x, self._y)

    @property
    def velocity(self) -> tuple[float, float]:
        return (self._vx, self._vy)

    @property
    def theta(self) -> float:
        return self._theta

    @property
    def omega(self) -> float:
        return self._omega

    @property
    def home(self) -> tuple[float, float]:
        return self._home

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    @property
    def grabbed(self) -> bool:
        return self._grab_local is not None

    @property
    def config(self) -> RigidBodyConfig:
        return self._cfg

    # ----- mutators -----
    def set_home(self, x: float, y: float) -> None:
        if (x, y) == self._home:
            return
        self._home = (x, y)
        self._wake()

    def snap_home(self, x: float, y: float) -> None:
        """Teleport the body to a new home with zeroed velocity. Used
        when the dock anchor moves under the buddy without a drag (e.g.
        screen-edge change) and we don't want the move to register as
        a flick."""
        self._home = (x, y)
        self._x, self._y = x, y
        self._theta = 0.0
        self._vx = self._vy = self._omega = 0.0
        self._grab_impulse_acc = (0.0, 0.0)
        self._settled_ticks = 0
        self._sleeping = True

    def begin_grab(
        self,
        local_x: float,
        local_y: float,
        target_x: float,
        target_y: float,
    ) -> None:
        """Start a grab at a body-local anchor with the cursor at
        ``(target_x, target_y)`` in world coords. ``local_x/y`` is the
        offset from COM at the moment of grab — pass the body-frame
        coords of the pixel under the cursor."""
        self._grab_local = (local_x, local_y)
        self._grab_target = (target_x, target_y)
        self._grab_impulse_acc = (0.0, 0.0)
        self._wake()

    def set_grab_target(self, x: float, y: float) -> None:
        if self._grab_local is None:
            return
        self._grab_target = (x, y)
        self._wake()

    def end_grab(self) -> None:
        """Release. Body coasts with its current ``(v, ω)`` — the home
        spring will pull it back. No fling impulse is injected; the
        constraint already integrated cursor velocity into the body's
        state every tick during the drag."""
        self._grab_local = None
        self._grab_target = None
        self._grab_impulse_acc = (0.0, 0.0)
        self._wake()

    def apply_impulse(
        self,
        px: float,
        py: float,
        at_local: tuple[float, float] | None = None,
    ) -> None:
        """Add a linear impulse at a body-local point (default COM).
        Off-COM impulses contribute angular momentum via ``r × P``."""
        cfg = self._cfg
        self._vx += px / cfg.mass
        self._vy += py / cfg.mass
        if at_local is not None:
            rx, ry = self._world_offset(at_local)
            self._omega += (rx * py - ry * px) / cfg.inertia
        self._clamp_velocity()
        self._wake()

    # ----- tick -----
    def tick(self, dt: float) -> None:
        if dt <= 0 or self._sleeping:
            return
        cfg = self._cfg
        m = cfg.mass
        inertia = cfg.inertia

        # Home spring: soft spring-damper on (x, y) toward home and
        # rotational spring on θ toward 0. Applied as direct
        # acceleration in semi-implicit Euler.
        omega_h = 2.0 * math.pi * cfg.home_frequency_hz
        k_lin = m * omega_h * omega_h
        c_lin = 2.0 * m * cfg.home_damping_ratio * omega_h
        k_rot = inertia * omega_h * omega_h
        c_rot = 2.0 * inertia * cfg.home_damping_ratio * omega_h
        hx, hy = self._home
        ax = (-k_lin * (self._x - hx) - c_lin * self._vx) / m
        ay = (-k_lin * (self._y - hy) - c_lin * self._vy) / m
        alpha = (-k_rot * self._theta - c_rot * self._omega) / inertia

        # Velocity update (semi-implicit).
        self._vx += ax * dt
        self._vy += ay * dt
        self._omega += alpha * dt

        # Grab constraint, if active. Solved once per tick with γ
        # regularization on the diagonal of K and warm-started via
        # ``_grab_impulse_acc`` across ticks.
        if self._grab_local is not None and self._grab_target is not None:
            self._solve_grab_constraint(dt)

        self._clamp_velocity()

        # Position update.
        self._x += self._vx * dt
        self._y += self._vy * dt
        self._theta += self._omega * dt
        # Wrap θ to (-π, π] so repeated rotations don't grow unbounded.
        self._theta = (self._theta + math.pi) % (2.0 * math.pi) - math.pi

        self._check_settle()

    # ----- internals -----
    def _solve_grab_constraint(self, dt: float) -> None:
        """One Gauss-Seidel sweep of Catto's soft mouse-joint.

        Computes effective mass ``K = J·M⁻¹·Jᵀ`` (2×2) at the world-
        frame anchor offset ``r``, adds γ to the diagonal, solves for
        the impulse ``P`` that drives anchor velocity to zero with bias
        ``β·C`` (position correction) and warm-start ``γ·P_acc``, then
        applies ``P`` to (v, ω). Off-COM rotation falls out of
        ``ω += (r × P) / I`` automatically.

        Soft-constraint coefficients (Catto, Soft Constraints, GDC 2011)::

            ω_n = 2π · f
            k = m · ω_n²
            c = 2 · m · ζ · ω_n
            γ = 1 / (dt · (c + dt·k))
            β = dt · k · γ
        """
        assert self._grab_local is not None
        assert self._grab_target is not None
        cfg = self._cfg
        m = cfg.mass
        inertia = cfg.inertia
        omega_n = 2.0 * math.pi * cfg.grab_frequency_hz
        k_eff = m * omega_n * omega_n
        c_eff = 2.0 * m * cfg.grab_damping_ratio * omega_n
        denom = dt * (c_eff + dt * k_eff)
        if denom <= 0.0:
            return
        gamma = 1.0 / denom
        beta = dt * k_eff * gamma

        # World-frame offset COM → grab anchor.
        rx, ry = self._world_offset(self._grab_local)
        # Position error C and anchor velocity Cdot = v + ω × r.
        anchor_x = self._x + rx
        anchor_y = self._y + ry
        cx = anchor_x - self._grab_target[0]
        cy = anchor_y - self._grab_target[1]
        v_anchor_x = self._vx - self._omega * ry
        v_anchor_y = self._vy + self._omega * rx
        # K_soft = K + γ·I, where
        #   K[0][0] = 1/m + ry²/I
        #   K[0][1] = K[1][0] = -rx·ry/I
        #   K[1][1] = 1/m + rx²/I
        inv_m = 1.0 / m
        inv_inertia = 1.0 / inertia
        k11 = inv_m + ry * ry * inv_inertia + gamma
        k12 = -rx * ry * inv_inertia
        k22 = inv_m + rx * rx * inv_inertia + gamma
        det = k11 * k22 - k12 * k12
        if abs(det) < 1e-12:
            return
        inv_det = 1.0 / det
        # P = -K_soft⁻¹ · (Cdot + β·C + γ·P_acc)
        pacc_x, pacc_y = self._grab_impulse_acc
        rhs_x = v_anchor_x + beta * cx + gamma * pacc_x
        rhs_y = v_anchor_y + beta * cy + gamma * pacc_y
        px = -(k22 * rhs_x - k12 * rhs_y) * inv_det
        py = -(k11 * rhs_y - k12 * rhs_x) * inv_det
        # Apply: v += P/m, ω += (r × P)/I.
        self._vx += px * inv_m
        self._vy += py * inv_m
        self._omega += (rx * py - ry * px) * inv_inertia
        self._grab_impulse_acc = (pacc_x + px, pacc_y + py)

    def _world_offset(self, local: tuple[float, float]) -> tuple[float, float]:
        cos_t = math.cos(self._theta)
        sin_t = math.sin(self._theta)
        lx, ly = local
        return (cos_t * lx - sin_t * ly, sin_t * lx + cos_t * ly)

    def _wake(self) -> None:
        self._sleeping = False
        self._settled_ticks = 0

    def _clamp_velocity(self) -> None:
        cfg = self._cfg
        cap = cfg.max_linear_speed
        if self._vx > cap:
            self._vx = cap
        elif self._vx < -cap:
            self._vx = -cap
        if self._vy > cap:
            self._vy = cap
        elif self._vy < -cap:
            self._vy = -cap
        cap_w = cfg.max_angular_speed
        if self._omega > cap_w:
            self._omega = cap_w
        elif self._omega < -cap_w:
            self._omega = -cap_w

    def _check_settle(self) -> None:
        if self._grab_local is not None:
            # Never sleep while held.
            self._settled_ticks = 0
            return
        cfg = self._cfg
        speed = math.hypot(self._vx, self._vy)
        dist = math.hypot(self._x - self._home[0], self._y - self._home[1])
        if (
            speed < cfg.settle_speed
            and abs(self._omega) < cfg.settle_omega
            and dist < cfg.settle_distance
            and abs(self._theta) < cfg.settle_angle
        ):
            self._settled_ticks += 1
            if self._settled_ticks >= cfg.settle_ticks_required:
                self._x, self._y = self._home
                self._theta = 0.0
                self._vx = self._vy = self._omega = 0.0
                self._sleeping = True
        else:
            self._settled_ticks = 0


DEFAULT_RIGID_BODY_CONFIG = RigidBodyConfig()

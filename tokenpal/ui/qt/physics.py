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
    sim: DangleSimulator | RigidBodySimulator,
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


# Per-tick velocity-constraint iterations for the grab solver. Box2D's
# `b2World::Step` default; load-bearing for holding the body against
# gravity (one sweep can't overcome any persistent external force).
_GRAB_VELOCITY_ITERATIONS = 8


# ---------------------------------------------------------------------------
# RigidBodySimulator — mouse-joint style 2D rigid body.
# Drives the dangleable Qt buddy. One body, one soft constraint.
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
    # Angular home spring: returns θ → 0 (upright) when not grabbed.
    # Linear has NO home spring — body slides under inertia after a
    # toss, decelerated only by ``drag_damping_*``, and settles
    # wherever momentum runs out. ``home_damping_ratio`` is sized
    # so ``c_home + c_drag`` adds up to roughly critical for angular
    # motion post-release (drag ≈ 0.3 ζ + home ≈ 0.7 ζ ≈ 1.0).
    home_frequency_hz: float = 3.0
    home_damping_ratio: float = 0.7
    # Always-on damping (active during grab too). Damping only — no
    # spring. The underlying coefficient is ``c = 2·m·ζ·ω_n`` (linear)
    # or ``c = 2·I·ζ·ω_n`` (angular). Linear and angular are split:
    #
    # - Linear: this is what decelerates a post-toss slide. Lower =
    #   longer slide. At 0.4 Hz, ζ=1, τ ≈ 0.4 s, so v=2000 px/s
    #   slides ~ 800 px before stopping.
    # - Angular: bleeds ω during sustained cursor twirls and tames
    #   the pendulum-while-held. Higher than linear by design — we
    #   want a long linear slide but a settled, mostly-critically-
    #   damped pendulum dangle.
    linear_drag_frequency_hz: float = 0.4
    angular_drag_frequency_hz: float = 1.0
    drag_damping_ratio: float = 1.0
    # Body inertia. `inertia` is the scalar moment of inertia I; for a
    # uniform disk of radius R, I = m·R²/2. We treat the buddy art as
    # such a disk; the off-COM grab dynamics emerge from the Jacobian
    # without needing a more accurate mass distribution.
    mass: float = 1.0
    inertia: float = 4000.0
    # Velocity caps so a hard fling can't slingshot off-screen / spin
    # faster than the integrator stays stable. Angular cap is set
    # below the post-release recovery's "easy" decay range so a
    # vigorous twirl doesn't take a perceptible "still spinning"
    # tail to settle (15 rad/s ≈ 2.4 revolutions/sec).
    max_linear_speed: float = 4000.0
    max_angular_speed: float = 15.0
    # Downward gravitational acceleration (px/s²), active ONLY during
    # a grab. Gives real-pendulum dangle: with the constraint pinning
    # the grab anchor to the cursor, gravity pulls COM to "below the
    # cursor" and the body rotates until COM hangs directly below the
    # grab point. Disabled when not grabbed so the body doesn't sag
    # below ``home`` after release.
    gravity: float = 12000.0
    # Distance-gated upright bias: a soft preferred-orientation torque
    # that pulls θ → 0 for grabs near COM, fading to zero past
    # ``upright_bias_radius`` so foot/ear grabs still dangle freely.
    # Without this, grabbing at the geometric center (which is below
    # COM in the head-heavy model — see ``_COM_Y_FRACTION``) creates
    # an unstable inverted-pendulum equilibrium and the body flips
    # 180° to put COM below the grab anchor. The bias mimics the
    # stabilizing effect distributed mass would provide: near-balance
    # grabs feel stable, off-balance grabs dangle.
    #
    # ``strength`` is the angular spring constant at zero grab offset
    # (units: torque per radian). ``radius`` is the body-frame grab
    # distance at which bias is zero (linear fade in between). For a
    # head-heavy buddy with COM at art_h*0.30, the visual center is
    # ~art_h*0.20 below COM (~25 px), so radius ≈ 30 captures center
    # grabs without bleeding into the limbs.
    upright_bias_strength: float = 500000.0
    upright_bias_radius: float = 30.0
    upright_bias_damping_ratio: float = 1.0
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
        # world coords. Constraint impulse is reset per-tick inside
        # the solver (no cross-tick warm-start) so γ·P_acc can't grow
        # unbounded under sustained gravity.
        self._grab_local: tuple[float, float] | None = None
        self._grab_target: tuple[float, float] | None = None
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
    def set_config(self, cfg: RigidBodyConfig) -> None:
        """Replace the active config (length, force, and inertia params).
        Used after a zoom change so gravity / inertia / settle thresholds
        scale to the new buddy size in lockstep."""
        self._cfg = cfg

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
        self._wake()

    def set_grab_target(self, x: float, y: float) -> None:
        if self._grab_local is None:
            return
        self._grab_target = (x, y)
        self._wake()

    def end_grab(self) -> None:
        """Release. Body coasts with its current ``(v, ω)`` — the home
        spring catches any residual motion. No fling impulse is
        injected; the constraint already integrated cursor velocity
        into the body's state every tick during the drag.

        Home is snapped to the current body position so the buddy
        stays where he was dropped — without this the buddy springs
        back to spawn on every release, which feels broken. Edge-dock
        can still re-aim home at a screen edge afterward."""
        self._grab_local = None
        self._grab_target = None
        self._home = (self._x, self._y)
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

        # Always-on damping (linear + angular, decoupled). Active
        # during both grab and free fall. Linear is loose so post-
        # toss inertia visibly slides; angular is tighter so the
        # pendulum-while-held feels settled and a vigorous twirl
        # bleeds off cleanly.
        omega_d_lin = 2.0 * math.pi * cfg.linear_drag_frequency_hz
        omega_d_rot = 2.0 * math.pi * cfg.angular_drag_frequency_hz
        c_lin_drag = 2.0 * m * cfg.drag_damping_ratio * omega_d_lin
        c_rot_drag = 2.0 * inertia * cfg.drag_damping_ratio * omega_d_rot
        self._vx -= (c_lin_drag * self._vx / m) * dt
        self._vy -= (c_lin_drag * self._vy / m) * dt
        self._omega -= (c_rot_drag * self._omega / inertia) * dt

        # Gravity is only active during a grab — that's what gives the
        # real-pendulum dangle (COM hangs below the grab point under
        # the constraint pin). When not grabbed, the home spring
        # owns position, and gravity would just sag the body below
        # home with no benefit.
        if self._grab_local is not None:
            self._vy += cfg.gravity * dt
            # Distance-gated upright bias. Center-of-COM grabs get
            # full restoring torque toward θ=0 (prevents the inverted-
            # pendulum flip); far-COM grabs get nothing and dangle
            # freely under gravity.
            grab_r = math.hypot(self._grab_local[0], self._grab_local[1])
            bias_factor = max(0.0, 1.0 - grab_r / cfg.upright_bias_radius)
            if bias_factor > 0.0:
                k_bias = cfg.upright_bias_strength
                omega_b = math.sqrt(k_bias / inertia)
                c_bias = (
                    2.0 * inertia * cfg.upright_bias_damping_ratio * omega_b
                )
                alpha_bias = (
                    bias_factor
                    * (-k_bias * self._theta - c_bias * self._omega)
                    / inertia
                )
                self._omega += alpha_bias * dt

        # Angular home spring: returns θ → 0 (upright) when not
        # grabbed. NO linear home spring — body slides under inertia
        # after release and settles wherever the always-on linear
        # damping catches it (gives the "toss → slight slide" feel
        # of a real object on a table). Mouse joints in real engines
        # never have a competing return-to-origin force during a
        # hold either, so this is gated on grab regardless.
        if self._grab_local is None:
            omega_h = 2.0 * math.pi * cfg.home_frequency_hz
            k_rot = inertia * omega_h * omega_h
            c_rot = 2.0 * inertia * cfg.home_damping_ratio * omega_h
            alpha = (-k_rot * self._theta - c_rot * self._omega) / inertia
            self._omega += alpha * dt

        # Grab constraint, if active. Iterated to convergence inside
        # the solver (Box2D-style, 8 sweeps) so the body holds against
        # gravity and other persistent forces.
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
        """Catto's soft mouse-joint, iterated to convergence.

        Computes effective mass ``K = J·M⁻¹·Jᵀ`` (2×2) at the world-
        frame anchor offset ``r``, adds γ to the diagonal, then runs
        eight Gauss-Seidel sweeps that each compute a corrective
        impulse ``P`` from the current ``(Cdot + β·C + γ·P_acc)`` and
        accumulate it into ``P_acc``. Off-COM rotation falls out of
        ``ω += (r × P) / I`` automatically.

        **Iteration count is load-bearing.** A single sweep can't
        overcome a persistent external force like gravity — the soft
        constraint is mathematically iterative. Box2D ships with 8
        velocity iterations per `b2World::Step` for exactly this
        reason; without iteration the body drifts under gravity by
        ~3-4 px/tick. See research findings in plans/fling-me.md.

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

        # World-frame offset COM → grab anchor. Constant within the
        # iteration loop — θ doesn't change until position update.
        rx, ry = self._world_offset(self._grab_local)
        # Position error C is also constant within the loop (position
        # only updates after constraint solve). β·C is the Baumgarte
        # bias driving the anchor back to the target over time.
        anchor_x = self._x + rx
        anchor_y = self._y + ry
        cx = anchor_x - self._grab_target[0]
        cy = anchor_y - self._grab_target[1]
        # K_soft = K + γ·I, inverted once outside the loop.
        inv_m = 1.0 / m
        inv_inertia = 1.0 / inertia
        k11 = inv_m + ry * ry * inv_inertia + gamma
        k12 = -rx * ry * inv_inertia
        k22 = inv_m + rx * rx * inv_inertia + gamma
        det = k11 * k22 - k12 * k12
        if abs(det) < 1e-12:
            return
        inv_det = 1.0 / det

        # Impulse accumulator resets per tick. Box2D warm-starts
        # across ticks but pairs that with a maxForce clamp to bound
        # ``m_impulse``; without the clamp, γ·P_acc grows unbounded
        # over many ticks under sustained external load (gravity)
        # and the solver becomes unstable. Resetting per tick gives
        # the same convergence behavior as ``b2World::Step`` with
        # warm-starting disabled, which is what most tutorials
        # default to.
        pacc_x = 0.0
        pacc_y = 0.0
        for _ in range(_GRAB_VELOCITY_ITERATIONS):
            # Cdot = v_anchor = v_COM + ω × r — recomputed each
            # iteration so successive impulses see the corrected
            # velocity from the previous sweep.
            v_anchor_x = self._vx - self._omega * ry
            v_anchor_y = self._vy + self._omega * rx
            rhs_x = v_anchor_x + beta * cx + gamma * pacc_x
            rhs_y = v_anchor_y + beta * cy + gamma * pacc_y
            # P = -K_soft⁻¹ · rhs
            px = -(k22 * rhs_x - k12 * rhs_y) * inv_det
            py = -(k11 * rhs_y - k12 * rhs_x) * inv_det
            self._vx += px * inv_m
            self._vy += py * inv_m
            self._omega += (rx * py - ry * px) * inv_inertia
            pacc_x += px
            pacc_y += py

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
        # No distance-from-home check — there's no linear home spring,
        # so the body settles wherever its slide runs out. Settle on
        # speed + angular state alone, then update ``home`` to the
        # final resting position so edge-dock and external callers
        # can still treat it as "where the buddy lives now."
        if (
            speed < cfg.settle_speed
            and abs(self._omega) < cfg.settle_omega
            and abs(self._theta) < cfg.settle_angle
        ):
            self._settled_ticks += 1
            if self._settled_ticks >= cfg.settle_ticks_required:
                self._theta = 0.0
                self._vx = self._vy = self._omega = 0.0
                self._home = (self._x, self._y)
                self._sleeping = True
        else:
            self._settled_ticks = 0


DEFAULT_RIGID_BODY_CONFIG = RigidBodyConfig()

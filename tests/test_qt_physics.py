"""Unit tests for the spring-pendulum integrator.

No Qt imports — this is pure-Python logic. Tests validate the v1
behavior described in plans/new-ui-new-me.md §"Dangle-able" v1:

- buddy comes to rest below the anchor with a predictable droop
- settle happens within ~1.5s at default tuning
- sleep state halts further evolution
- set_anchor / apply_impulse wake the simulator
- velocity is clamped so a fling can't slingshot off-screen
"""

from __future__ import annotations

import math

import pytest

from tokenpal.ui.qt.physics import (
    DangleSimulator,
    PhysicsConfig,
    RigidBodyConfig,
    RigidBodySimulator,
    run_until_settled,
)


def test_rest_position_sits_below_anchor_by_expected_droop() -> None:
    cfg = PhysicsConfig()
    sim = DangleSimulator(anchor=(100.0, 50.0), initial_pos=(100.0, 50.0), config=cfg)
    rest_x, rest_y = sim.rest_position()
    assert rest_x == pytest.approx(100.0)
    assert rest_y == pytest.approx(50.0 + cfg.gravity * cfg.mass / cfg.spring_k)


def test_released_from_anchor_settles_to_rest() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(0.0, 0.0))
    ticks = run_until_settled(sim)
    rest = sim.rest_position()
    assert sim.sleeping
    assert sim.position == rest
    # Default tuning aims for ~1.5s settle. 60Hz * 1.5s = 90 ticks; give
    # 2x headroom before we'd consider the defaults miscalibrated.
    assert ticks < 180, f"settle took {ticks} ticks; defaults likely drifted"


def test_settles_within_1_5s_from_a_hard_displacement() -> None:
    """Drop the buddy 50px sideways from rest and 30px above — verify it
    actually comes to rest in the 1.5s budget the plan committed to."""
    sim = DangleSimulator(
        anchor=(0.0, 0.0),
        initial_pos=(50.0, -30.0),
    )
    ticks = run_until_settled(sim, max_ticks=180)
    assert ticks <= 90 + 30, (  # 1.5s + 0.5s grace
        f"settle took {ticks} ticks from hard displacement"
    )


def test_impulse_wakes_sleeping_sim_and_imparts_velocity() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(0.0, 0.0))
    run_until_settled(sim)
    assert sim.sleeping

    sim.apply_impulse(vx=500.0, vy=-200.0)
    assert not sim.sleeping
    vx, vy = sim.velocity
    assert vx == pytest.approx(500.0)
    assert vy == pytest.approx(-200.0)


def test_set_anchor_wakes_sim_and_drags_body() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(0.0, 0.0))
    run_until_settled(sim)
    rest_before = sim.rest_position()

    sim.set_anchor(200.0, 0.0)
    assert not sim.sleeping

    # After a few ticks the buddy should be moving toward the new anchor.
    for _ in range(10):
        sim.tick(1.0 / 60.0)
    new_pos_x, _ = sim.position
    assert new_pos_x > rest_before[0], (
        "body should accelerate toward new anchor"
    )


def test_tick_is_noop_when_sleeping() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(0.0, 0.0))
    run_until_settled(sim)
    pos_before = sim.position
    for _ in range(100):
        sim.tick(1.0 / 60.0)
    assert sim.position == pos_before
    assert sim.sleeping


def test_velocity_is_clamped_by_max_speed() -> None:
    cfg = PhysicsConfig(max_speed=500.0)
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(0.0, 0.0), config=cfg)
    sim.apply_impulse(vx=10_000.0, vy=0.0)
    vx, _ = sim.velocity
    assert abs(vx) <= cfg.max_speed


def test_semi_implicit_euler_is_stable_at_60hz() -> None:
    """Integrate for a long time — the spring shouldn't blow up."""
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(100.0, 100.0))
    for _ in range(10_000):  # ~166 seconds at 60Hz
        sim.tick(1.0 / 60.0)
    x, y = sim.position
    assert math.isfinite(x)
    assert math.isfinite(y)
    rest_x, rest_y = sim.rest_position()
    # Should be near rest — definitely not diverged to infinity.
    assert math.hypot(x - rest_x, y - rest_y) < 1.0


def test_ignores_non_positive_dt() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(50.0, 0.0))
    pos_before = sim.position
    sim.tick(0.0)
    sim.tick(-0.1)
    assert sim.position == pos_before


def test_zero_dt_tick_returns_position_without_mutating() -> None:
    sim = DangleSimulator(anchor=(0.0, 0.0), initial_pos=(10.0, 20.0))
    assert sim.tick(0.0) == (10.0, 20.0)


def test_custom_config_changes_rest_offset() -> None:
    tight = PhysicsConfig(spring_k=600.0)
    slack = PhysicsConfig(spring_k=60.0)
    t_sim = DangleSimulator(anchor=(0.0, 0.0), config=tight)
    s_sim = DangleSimulator(anchor=(0.0, 0.0), config=slack)
    _, t_y = t_sim.rest_position()
    _, s_y = s_sim.rest_position()
    assert t_y < s_y, "stiffer spring should yield a smaller droop"


def test_run_until_settled_raises_when_budget_exceeded() -> None:
    # Configure an absurdly slow sim that can't possibly settle in
    # 5 ticks, and verify the helper reports the problem.
    cfg = PhysicsConfig(damping=0.01, settle_ticks_required=2)
    sim = DangleSimulator(
        anchor=(0.0, 0.0), initial_pos=(500.0, 0.0), config=cfg,
    )
    with pytest.raises(RuntimeError, match="did not settle"):
        run_until_settled(sim, max_ticks=5)


# -----------------------------------------------------------------------------
# RigidBodySimulator tests — the mouse-joint model that drives the
# Qt buddy. Validates the soft-constraint solver, off-COM parallel-axis
# behavior, release-coast (no double-counted impulse), and home-spring
# settle.
# -----------------------------------------------------------------------------


def _drive_grab_target_circle(
    sim: RigidBodySimulator,
    center: tuple[float, float],
    radius: float,
    omega_cursor: float,
    duration: float,
    *,
    dt: float = 1.0 / 60.0,
    sample_after: float | None = None,
) -> list[tuple[float, float, float]]:
    """Drive the grab target around ``center`` at ``omega_cursor`` for
    ``duration`` seconds. Returns ``(x, y, theta)`` samples taken
    after ``sample_after`` seconds (empty list if None)."""
    cx0, cy0 = center
    samples: list[tuple[float, float, float]] = []
    for i in range(int(duration / dt)):
        t = i * dt
        sim.set_grab_target(
            cx0 + radius * math.cos(omega_cursor * t),
            cy0 + radius * math.sin(omega_cursor * t),
        )
        sim.tick(dt)
        if sample_after is not None and t > sample_after:
            samples.append((sim.position[0], sim.position[1], sim.theta))
    return samples


def test_rigid_body_starts_sleeping_at_home() -> None:
    sim = RigidBodySimulator(home=(123.0, 456.0))
    assert sim.position == (123.0, 456.0)
    assert sim.theta == 0.0
    assert sim.velocity == (0.0, 0.0)
    assert sim.omega == 0.0
    assert sim.sleeping
    assert not sim.grabbed


def test_rigid_body_begin_grab_wakes_simulator() -> None:
    sim = RigidBodySimulator(home=(0.0, 0.0))
    assert sim.sleeping
    sim.begin_grab(local_x=10.0, local_y=0.0, target_x=20.0, target_y=0.0)
    assert sim.grabbed
    assert not sim.sleeping


def test_rigid_body_grab_pulls_anchor_toward_target() -> None:
    """Grab at body-local (0, 0), target offset 50 px right. After a few
    ticks the body should have moved toward the target."""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.begin_grab(local_x=0.0, local_y=0.0, target_x=50.0, target_y=0.0)
    for _ in range(30):  # 0.5 s
        sim.tick(1.0 / 60.0)
    # COM should have moved meaningfully toward the cursor.
    assert sim.position[0] > 5.0


def test_rigid_body_off_com_grab_rotates_more_than_com_grab() -> None:
    """Two simulators, identical input, but one grabs at the body edge
    and one at COM. The off-COM grab should accumulate noticeably more
    angular velocity, because the constraint impulse's ``r × P`` term
    is non-zero only off-COM. Validates the parallel-axis effect falls
    out of the Jacobian without explicit handling.
    """
    edge = RigidBodySimulator(home=(0.0, 0.0))
    com = RigidBodySimulator(home=(0.0, 0.0))
    edge.begin_grab(local_x=40.0, local_y=0.0, target_x=0.0, target_y=0.0)
    com.begin_grab(local_x=0.0, local_y=0.0, target_x=0.0, target_y=0.0)
    # Drive both targets sideways for 0.25 s.
    dt = 1.0 / 60.0
    for i in range(15):
        target_y = 50.0 * (i + 1) / 15.0
        edge.set_grab_target(0.0, target_y)
        com.set_grab_target(0.0, target_y)
        edge.tick(dt)
        com.tick(dt)
    # Edge grab gets r × P angular kick; COM grab does not.
    assert abs(edge.omega) > abs(com.omega) + 0.1, (
        f"off-COM ω={edge.omega}, COM ω={com.omega} — "
        "expected off-COM grab to accumulate more rotation"
    )


def test_rigid_body_release_does_not_inject_a_velocity_jump() -> None:
    """Spin the body up via the grab constraint, then release. The
    body's velocity must not change discontinuously on ``end_grab``;
    it should retain whatever (v, ω) it accumulated. This is the
    regression test for the old PendulumSimulator's
    ``_inject_fling_impulse`` double-counting failure mode."""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.begin_grab(local_x=30.0, local_y=0.0, target_x=0.0, target_y=0.0)
    # Drive target in a tight circle to spin the body up.
    dt = 1.0 / 60.0
    for i in range(60):  # 1 s
        t = i * dt
        sim.set_grab_target(
            40.0 * math.cos(6.0 * t),
            40.0 * math.sin(6.0 * t),
        )
        sim.tick(dt)
    vx_before, vy_before = sim.velocity
    omega_before = sim.omega
    sim.end_grab()
    # No tick between end_grab and the assertion: velocity should be
    # exactly preserved.
    assert sim.velocity == (vx_before, vy_before)
    assert sim.omega == omega_before
    assert not sim.grabbed


def test_rigid_body_click_and_release_settles_quickly() -> None:
    """Click and immediately release without dragging. Body shouldn't
    swing; with critically-damped home spring it should settle in ≤ 1
    oscillation cycle."""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.begin_grab(local_x=20.0, local_y=0.0, target_x=20.0, target_y=0.0)
    sim.tick(1.0 / 60.0)
    sim.end_grab()
    # ζ=1 at f=2 Hz settles in ~0.3 s; give 1 s of budget.
    ticks = run_until_settled(sim, max_ticks=120)
    assert sim.sleeping
    assert ticks <= 120


def test_rigid_body_home_spring_returns_displaced_body() -> None:
    """Body displaced from home with no grab should return to home and
    sleep within the home-spring settle time."""
    sim = RigidBodySimulator(home=(100.0, 100.0))
    # Push the body away.
    sim.apply_impulse(px=200.0, py=-150.0)
    ticks = run_until_settled(sim, max_ticks=600)
    assert sim.sleeping
    assert sim.position == (100.0, 100.0)
    assert sim.theta == 0.0
    # ζ=1 at f=2 Hz: settle time ~0.5 s = 30 ticks; allow 4× headroom.
    assert ticks < 240


def test_rigid_body_stable_at_60hz_with_grab_default_tuning() -> None:
    """Default tuning (8 Hz, ζ=1.0) at 60 Hz must not blow up over 10 s
    of constant-target grab. No NaN, no exponential divergence."""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.begin_grab(local_x=50.0, local_y=0.0, target_x=200.0, target_y=300.0)
    for _ in range(600):
        sim.tick(1.0 / 60.0)
    x, y = sim.position
    assert math.isfinite(x)
    assert math.isfinite(y)
    assert math.isfinite(sim.theta)
    assert math.isfinite(sim.omega)
    # Body should be near the target (constraint converged).
    assert math.hypot(x - 100.0, y - 100.0) < 500.0  # rough sanity bound


def test_rigid_body_grab_target_circle_yo_yo_lock() -> None:
    """Cursor circles the buddy's COM; body's grab anchor should track
    cursor within tolerance after settle. Validates that the constraint
    follows a moving target without runaway. (Less strict than the
    old pendulum yo-yo test — under a soft constraint the anchor
    *converges* to the target rather than locking 1:1 in ω.)"""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.begin_grab(local_x=30.0, local_y=0.0, target_x=80.0, target_y=0.0)
    samples = _drive_grab_target_circle(
        sim,
        center=(0.0, 0.0),
        radius=80.0,
        omega_cursor=2.0,
        duration=4.0,
        sample_after=2.0,
    )
    # Body's position trajectory should bound the cursor circle.
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]
    rs = [math.hypot(x, y) for x, y in zip(xs, ys, strict=False)]
    assert max(rs) < 200.0, "body diverged from cursor circle"
    assert min(rs) > 5.0, "body collapsed to origin"


def test_rigid_body_apply_impulse_off_com_rotates() -> None:
    """Linear impulse applied at off-COM body point should produce
    angular velocity proportional to ``r × P``."""
    sim = RigidBodySimulator(home=(0.0, 0.0))
    # Push +y at a body-local point offset +x from COM. r × P with
    # r=(40, 0), P=(0, 100) gives positive z-component → +ω.
    sim.apply_impulse(px=0.0, py=100.0, at_local=(40.0, 0.0))
    assert sim.omega > 0.0
    vx, vy = sim.velocity
    assert vx == pytest.approx(0.0)
    assert vy > 0.0


def test_rigid_body_ignores_non_positive_dt() -> None:
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.apply_impulse(px=100.0, py=0.0)
    vx_before = sim.velocity[0]
    sim.tick(0.0)
    sim.tick(-0.1)
    # Velocity must not have integrated away.
    assert sim.velocity[0] == pytest.approx(vx_before)


def test_rigid_body_set_grab_target_is_noop_without_active_grab() -> None:
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.set_grab_target(500.0, 500.0)
    # No grab active — call should be a noop, no wake.
    assert sim.sleeping
    assert not sim.grabbed


def test_rigid_body_snap_home_resets_state() -> None:
    sim = RigidBodySimulator(home=(0.0, 0.0))
    sim.apply_impulse(px=300.0, py=0.0, at_local=(20.0, 0.0))
    sim.tick(1.0 / 60.0)
    sim.snap_home(500.0, 500.0)
    assert sim.position == (500.0, 500.0)
    assert sim.theta == 0.0
    assert sim.velocity == (0.0, 0.0)
    assert sim.omega == 0.0
    assert sim.sleeping


def test_rigid_body_custom_config_overrides_defaults() -> None:
    """Stiffer grab spring should pull the body harder for a given
    target offset, so the body reaches the target faster."""
    soft_cfg = RigidBodyConfig(grab_frequency_hz=4.0, grab_damping_ratio=1.0)
    stiff_cfg = RigidBodyConfig(grab_frequency_hz=12.0, grab_damping_ratio=1.0)
    soft = RigidBodySimulator(home=(0.0, 0.0), config=soft_cfg)
    stiff = RigidBodySimulator(home=(0.0, 0.0), config=stiff_cfg)
    soft.begin_grab(local_x=0.0, local_y=0.0, target_x=100.0, target_y=0.0)
    stiff.begin_grab(local_x=0.0, local_y=0.0, target_x=100.0, target_y=0.0)
    for _ in range(10):  # 0.16 s — short enough to differentiate
        soft.tick(1.0 / 60.0)
        stiff.tick(1.0 / 60.0)
    assert stiff.position[0] > soft.position[0]
    assert soft.config is soft_cfg
    assert stiff.config is stiff_cfg

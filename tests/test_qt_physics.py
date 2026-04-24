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
    PendulumConfig,
    PendulumSimulator,
    PhysicsConfig,
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
# PendulumSimulator tests — the rigid-pendulum model BuddyWindow uses.
# -----------------------------------------------------------------------------


def _tick_pendulum(sim: PendulumSimulator, ticks: int, dt: float = 1.0 / 60.0) -> None:
    for _ in range(ticks):
        sim.tick(dt)


def test_pendulum_starts_at_rest_and_stays_put_when_pivot_is_stationary() -> None:
    sim = PendulumSimulator(pivot=(100.0, 100.0), length=150.0)
    _tick_pendulum(sim, 30)
    assert sim.theta == pytest.approx(0.0, abs=1e-3)
    assert sim.theta_dot == pytest.approx(0.0, abs=1e-3)
    assert sim.sleeping


def test_pendulum_sleeps_after_a_swing_and_resists_further_ticks() -> None:
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=150.0)
    sim.apply_angular_impulse(5.0)
    ticks = run_until_settled(sim, max_ticks=600)
    assert sim.sleeping
    assert sim.theta == pytest.approx(0.0, abs=1e-3)
    # Further ticks must not wake it.
    theta_before = sim.theta
    _tick_pendulum(sim, 100)
    assert sim.theta == theta_before
    # Underdamped tuning should show at least one visible oscillation, so
    # 20 ticks (1/3s) isn't enough and 600 ticks is plenty.
    assert 20 < ticks < 600


def test_pivot_moving_right_makes_feet_trail_left() -> None:
    """Drive the pivot rightward; check theta trends positive, which in
    render terms (Qt rotate CW = feet-left for a top-pivot) means the
    buddy's feet are trailing left behind the cursor."""
    dt = 1.0 / 60.0
    sim = PendulumSimulator(
        pivot=(0.0, 0.0),
        length=150.0,
        config=PendulumConfig(damping=2.0),
    )
    px = 0.0
    vx = 0.0
    for _ in range(15):  # 0.25 s
        vx += 6000.0 * dt
        px += vx * dt
        sim.set_pivot(px, 0.0)
        sim.tick(dt)
    assert sim.theta > 0.1, f"expected feet to trail left; theta={sim.theta}"


def test_pivot_moving_left_makes_feet_trail_right() -> None:
    dt = 1.0 / 60.0
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=150.0)
    px = 0.0
    vx = 0.0
    for _ in range(15):
        vx -= 6000.0 * dt
        px += vx * dt
        sim.set_pivot(px, 0.0)
        sim.tick(dt)
    assert sim.theta < -0.1, f"expected feet to trail right; theta={sim.theta}"


def test_angular_impulse_decays_toward_zero() -> None:
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=150.0)
    sim.apply_angular_impulse(4.0)
    peak = 0.0
    for _ in range(60):  # 1 s
        sim.tick(1.0 / 60.0)
        peak = max(peak, abs(sim.theta))
    assert peak > 0.05, "impulse should produce a visible swing"
    # After another 3s of damped motion, amplitude should be well below peak.
    for _ in range(180):
        sim.tick(1.0 / 60.0)
    assert abs(sim.theta) < peak * 0.1 or sim.sleeping


def test_set_pivot_is_noop_when_same_point() -> None:
    sim = PendulumSimulator(pivot=(50.0, 50.0), length=100.0)
    run_until_settled(sim)
    assert sim.sleeping
    sim.set_pivot(50.0, 50.0)  # identical — should not wake
    assert sim.sleeping


def test_set_pivot_wakes_sleeping_sim() -> None:
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=100.0)
    run_until_settled(sim)
    assert sim.sleeping
    sim.set_pivot(100.0, 0.0)
    assert not sim.sleeping


def test_pendulum_clamps_angular_speed() -> None:
    cfg = PendulumConfig(max_angular_speed=3.0)
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=100.0, config=cfg)
    sim.apply_angular_impulse(100.0)
    assert abs(sim.theta_dot) <= cfg.max_angular_speed


def test_pendulum_ignores_non_positive_dt() -> None:
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=100.0)
    sim.apply_angular_impulse(1.0)
    sim.tick(0.0)
    sim.tick(-0.1)
    # Angular velocity shouldn't have been integrated away by bogus ticks.
    assert sim.theta_dot == pytest.approx(1.0)


def test_pendulum_min_length_floor() -> None:
    cfg = PendulumConfig(min_length=20.0)
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=1.0, config=cfg)
    assert sim.length == 20.0
    sim.set_length(5.0)
    assert sim.length == 20.0
    sim.set_length(100.0)
    assert sim.length == 100.0


def test_pendulum_stable_over_long_integration() -> None:
    """Integrate 100s at 60Hz with constant pivot — theta shouldn't explode."""
    sim = PendulumSimulator(pivot=(0.0, 0.0), length=150.0)
    sim.apply_angular_impulse(2.0)
    for _ in range(6000):
        sim.tick(1.0 / 60.0)
    assert math.isfinite(sim.theta)
    assert math.isfinite(sim.theta_dot)
    assert abs(sim.theta) < 0.01

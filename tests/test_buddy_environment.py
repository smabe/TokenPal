"""Pure-logic tests for the buddy environment layer.

No Textual imports — these run fast and verify the math/behavior in isolation
from the overlay's render path.
"""

from __future__ import annotations

import random

import pytest

from tokenpal.ui.buddy_environment import (
    PARTICLE_LIMIT,
    BuddyMotion,
    EnvState,
    Kind,
    ParticleField,
    wmo_to_kind,
)

# --- wmo_to_kind ---


@pytest.mark.parametrize(
    "code,expected_kind",
    [
        (0, Kind.CLEAR),
        (1, Kind.CLEAR),
        (2, Kind.CLOUDY),
        (3, Kind.CLOUDY),
        (45, Kind.FOG),
        (51, Kind.DRIZZLE),
        (61, Kind.RAIN),
        (65, Kind.RAIN),
        (71, Kind.SNOW),
        (75, Kind.SNOW),
        (95, Kind.STORM),
        (99, Kind.STORM),
    ],
)
def test_wmo_to_kind_buckets(code: int, expected_kind: Kind) -> None:
    kind, intensity = wmo_to_kind(code)
    assert kind is expected_kind
    assert 0.0 <= intensity <= 1.0


def test_wmo_unknown_code_falls_back_to_clear() -> None:
    kind, intensity = wmo_to_kind(9999)
    assert kind is Kind.CLEAR
    assert intensity == 0.5


def test_wmo_none_falls_back_to_clear() -> None:
    kind, _ = wmo_to_kind(None)
    assert kind is Kind.CLEAR


# --- EnvState.from_inputs ---


def test_envstate_no_weather_idle_clear() -> None:
    s = EnvState.from_inputs(
        weather_data=None, idle_event=None, sensitive_suppressed=False,
    )
    assert s.kind is Kind.CLEAR
    assert s.hot_outside is False
    assert s.afk_active is False
    assert s.sensitive_suppressed is False


def test_envstate_hot_outside_fahrenheit() -> None:
    s = EnvState.from_inputs(
        weather_data={"temperature": 92, "unit": "°F", "weather_code": 0},
        idle_event=None,
        sensitive_suppressed=False,
    )
    assert s.hot_outside is True


def test_envstate_hot_outside_celsius_converts() -> None:
    # 32°C = 89.6°F → above 85 default threshold.
    s = EnvState.from_inputs(
        weather_data={"temperature": 32, "unit": "°C", "weather_code": 0},
        idle_event=None,
        sensitive_suppressed=False,
    )
    assert s.hot_outside is True


def test_envstate_cold_not_hot() -> None:
    s = EnvState.from_inputs(
        weather_data={"temperature": 50, "unit": "°F", "weather_code": 0},
        idle_event=None,
        sensitive_suppressed=False,
    )
    assert s.hot_outside is False


def test_envstate_sustained_idle_is_afk() -> None:
    s = EnvState.from_inputs(
        weather_data=None, idle_event="sustained", sensitive_suppressed=False,
    )
    assert s.afk_active is True


def test_envstate_is_day_at_noon() -> None:
    import datetime as dt

    s = EnvState.from_inputs(
        weather_data=None,
        idle_event=None,
        sensitive_suppressed=False,
        now=dt.datetime(2026, 4, 19, 12, 0),
    )
    assert s.is_day is True


def test_envstate_is_night_at_2am() -> None:
    import datetime as dt

    s = EnvState.from_inputs(
        weather_data=None,
        idle_event=None,
        sensitive_suppressed=False,
        now=dt.datetime(2026, 4, 19, 2, 0),
    )
    assert s.is_day is False


def test_prop_swaps_sun_for_moon_at_night() -> None:
    import datetime as dt

    from tokenpal.ui.ascii_props import MOON_SPRITE, SUN_SPRITE, prop_for

    clear_data = {"weather_code": 0, "temperature": 60, "unit": "°F"}
    day = EnvState.from_inputs(
        weather_data=clear_data, idle_event=None, sensitive_suppressed=False,
        now=dt.datetime(2026, 4, 19, 13, 0),
    )
    night = EnvState.from_inputs(
        weather_data=clear_data, idle_event=None, sensitive_suppressed=False,
        now=dt.datetime(2026, 4, 19, 22, 0),
    )
    assert prop_for(day) is SUN_SPRITE
    assert prop_for(night) is MOON_SPRITE


def test_envstate_other_idle_event_not_afk() -> None:
    s = EnvState.from_inputs(
        weather_data=None, idle_event="returned", sensitive_suppressed=False,
    )
    assert s.afk_active is False


# --- BuddyMotion ---


def _seeded_motion() -> BuddyMotion:
    return BuddyMotion(rng=random.Random(42), speed=10.0)


def test_motion_picks_target_and_slides() -> None:
    m = _seeded_motion()
    env = EnvState.from_inputs(
        weather_data=None, idle_event=None, sensitive_suppressed=False,
    )
    # First tick picks a target, advances toward it.
    m.tick(0.1, bounds_w=20.0, bounds_h=2.0, env=env)
    assert m.target_x > 0.0 or m.target_y >= 0.0
    # Position is bounded.
    assert 0.0 <= m.x <= 20.0
    assert 0.0 <= m.y <= 2.0


def test_motion_freezes_under_sensitive() -> None:
    m = _seeded_motion()
    env = EnvState.from_inputs(
        weather_data=None, idle_event=None, sensitive_suppressed=True,
    )
    for _ in range(20):
        m.tick(0.1, bounds_w=20.0, bounds_h=2.0, env=env)
    assert m.x == 0.0
    assert m.y == 0.0


def test_motion_clamps_to_bounds_on_resize() -> None:
    m = _seeded_motion()
    env = EnvState.from_inputs(
        weather_data=None, idle_event=None, sensitive_suppressed=False,
    )
    for _ in range(50):
        m.tick(0.1, bounds_w=40.0, bounds_h=2.0, env=env)
    # Now shrink the panel — buddy must clamp, not teleport off-screen.
    m.tick(0.1, bounds_w=10.0, bounds_h=2.0, env=env)
    assert m.x <= 10.0
    assert m.y <= 2.0


def test_motion_afk_slows() -> None:
    fast = _seeded_motion()
    slow = BuddyMotion(rng=random.Random(42), speed=10.0)
    env_active = EnvState.from_inputs(
        weather_data=None, idle_event=None, sensitive_suppressed=False,
    )
    env_afk = EnvState.from_inputs(
        weather_data=None, idle_event="sustained", sensitive_suppressed=False,
    )
    # Force same target by seeding identically and ticking once for both.
    fast.tick(0.1, 30.0, 2.0, env_active)
    slow.tick(0.1, 30.0, 2.0, env_afk)
    # Same RNG → same target. Active buddy moves further per tick than AFK.
    assert fast.target_x == slow.target_x
    moved_active = abs(fast.x - 0.0)
    moved_afk = abs(slow.x - 0.0)
    assert moved_active > moved_afk


# --- ParticleField ---


def _field() -> ParticleField:
    return ParticleField(rng=random.Random(7))


def test_particles_clear_day_spawns_dust() -> None:
    import datetime as dt

    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 0, "temperature": 65, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
        now=dt.datetime(2026, 4, 19, 13, 0),
    )
    for _ in range(60):
        field.tick(0.1, panel_w=30, panel_h=15, env=env, buddy_x=15.0, buddy_y=10.0)
    glyphs = {p.glyph for p in field.particles}
    assert glyphs.issubset({".", "·"})
    assert len(field.particles) > 0


def test_populate_starfield_places_static_pulsing_stars() -> None:
    field = _field()
    field.populate_starfield(30, 15, target_count=20)
    # Jittered-grid sampling caps at the number of available cells; for a
    # 30x10.5 sky targeting 20 stars, ~14 cells fit. Allow a range.
    assert 8 <= len(field.particles) <= 20
    for p in field.particles:
        assert p.vx == 0.0 and p.vy == 0.0
        assert p.pulse_palette
        assert p.glyph in {"*", "·", "✦", "+", "⋆", "✶", "°", "."}


def test_populate_starfield_enforces_minimum_spacing() -> None:
    field = _field()
    field.populate_starfield(60, 20, target_count=15)
    # No two stars closer than ~half a cell side.
    pts = [(p.x, p.y) for p in field.particles]
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1:]:
            d = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
            assert d > 1.5, f"stars too close: ({x1:.1f},{y1:.1f}) ↔ ({x2:.1f},{y2:.1f}) d={d:.2f}"


def test_populate_starfield_clamps_max_x() -> None:
    field = _field()
    field.populate_starfield(30, 15, target_count=20, max_x=18)
    for p in field.particles:
        assert p.x <= 18.0, f"star at x={p.x} exceeds max_x=18"


def test_populate_starfield_replaces_existing_stars() -> None:
    field = _field()
    field.populate_starfield(30, 15, target_count=10)
    first_positions = {(p.x, p.y) for p in field.particles}
    field.populate_starfield(30, 15, target_count=10)
    second_positions = {(p.x, p.y) for p in field.particles}
    # Same RNG seed, but populate clears and re-spawns — positions differ
    # because the RNG advanced past first run.
    assert first_positions != second_positions


def test_clear_stars_drops_stars_only() -> None:
    field = _field()
    field.populate_starfield(30, 15, target_count=5)
    # Add a non-star particle (dust)
    field._spawn_dust(30, 15)
    assert any(p.pulse_palette for p in field.particles)
    assert any(not p.pulse_palette for p in field.particles)
    field.clear_stars()
    assert all(not p.pulse_palette for p in field.particles)
    assert len(field.particles) >= 1  # dust survived


def test_stars_color_cycles_over_time() -> None:
    field = _field()
    field.populate_starfield(30, 15, target_count=10)
    seen: set[str] = set()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 0, "temperature": 50, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    for _ in range(120):
        field.tick(0.1, 30, 15, env, 15.0, 10.0)
        for p in field.particles:
            seen.add(p.color)
    assert len(seen) >= 3


def test_particles_rain_falls_downward() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 63, "temperature": 60, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    field.tick(0.5, 30, 15, env, 15.0, 10.0)
    rain = [p for p in field.particles if p.vy > 0 and p.color == "#5599ff"]
    assert len(rain) > 0


def test_particles_snow_drifts_horizontally() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 73, "temperature": 30, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    field.tick(1.0, 30, 15, env, 15.0, 10.0)
    snow = [p for p in field.particles if p.color == "#ddddff"]
    assert any(p.spin != 0.0 for p in snow)


def test_particles_storm_emits_lightning() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 99, "temperature": 70, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    # Pump enough time for lightning_accum to fire.
    for _ in range(40):
        field.tick(0.2, 30, 15, env, 15.0, 10.0)
    lightning = [p for p in field.particles if p.color == "#ffff66"]
    # We can't guarantee lightning landed in the final tick (life=0.25), but
    # over a few ticks something must have spawned. Assert spawn behavior by
    # running once more and checking accum-driven spawn.
    if not lightning:
        # Try a much larger pump.
        for _ in range(100):
            field.tick(0.2, 30, 15, env, 15.0, 10.0)
            if any(p.color == "#ffff66" for p in field.particles):
                lightning = [p for p in field.particles if p.color == "#ffff66"]
                break
    assert lightning, "lightning particles never spawned over 28 simulated seconds"


def test_particles_hot_emits_steam() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 0, "temperature": 95, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    field.tick(1.0, 30, 15, env, 15.0, 10.0)
    steam = [p for p in field.particles if p.glyph in ("~", "°")]
    assert len(steam) > 0


def test_particles_sensitive_freezes_field() -> None:
    field = _field()
    env_open = EnvState.from_inputs(
        weather_data={"weather_code": 63, "temperature": 60, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    for _ in range(10):
        field.tick(0.1, 30, 15, env_open, 15.0, 10.0)
    snapshot = [(p.x, p.y, p.life) for p in field.particles]
    assert snapshot, "field needs particles before freeze test"

    env_locked = EnvState.from_inputs(
        weather_data={"weather_code": 63, "temperature": 60, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=True,
    )
    for _ in range(10):
        field.tick(0.1, 30, 15, env_locked, 15.0, 10.0)
    after = [(p.x, p.y, p.life) for p in field.particles]
    assert snapshot == after


def test_particles_afk_slower_spawn_than_active() -> None:
    rain_data = {"weather_code": 63, "temperature": 60, "unit": "°F"}
    env_active = EnvState.from_inputs(
        weather_data=rain_data, idle_event=None, sensitive_suppressed=False,
    )
    env_afk = EnvState.from_inputs(
        weather_data=rain_data, idle_event="sustained", sensitive_suppressed=False,
    )
    field_active = ParticleField(rng=random.Random(7))
    field_afk = ParticleField(rng=random.Random(7))
    for _ in range(20):
        field_active.tick(0.1, 30, 15, env_active, 15.0, 10.0)
        field_afk.tick(0.1, 30, 15, env_afk, 15.0, 10.0)
    # AFK should have measurably fewer particles than active under same RNG.
    assert len(field_afk.particles) < len(field_active.particles)


def test_particles_cap_never_exceeded() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 99, "temperature": 95, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    for _ in range(200):
        field.tick(0.1, 30, 15, env, 15.0, 10.0)
        assert len(field.particles) <= PARTICLE_LIMIT


def test_particles_cull_when_off_panel() -> None:
    field = _field()
    env = EnvState.from_inputs(
        weather_data={"weather_code": 63, "temperature": 60, "unit": "°F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    # Spawn one rain drop with a tiny life and verify it culls cleanly.
    from tokenpal.ui.buddy_environment import Particle

    field.particles.append(Particle(
        x=5.0, y=5.0, vx=0.0, vy=0.0, ax=0.0, ay=0.0,
        life=0.05, glyph=".", color="#5599ff",
    ))
    field.tick(0.2, 30, 15, env, 15.0, 10.0)
    assert all(
        not (p.x == 5.0 and p.y == 5.0 and p.glyph == ".")
        for p in field.particles
    ), "expired particle should be culled"

"""Headless tests for the Qt weather sim.

The widgets (SkyWindow, BuddyRainOverlay) need a QApplication; the sim
itself is pure Python and exercised without Qt. Where we do touch a
widget we use the existing ``tests/test_qt_*.py`` pattern: manual qapp
fixture + module-level ``pytest.importorskip("PySide6")``, no
``QT_QPA_PLATFORM=offscreen`` env var (Qt auto-detects headless). See
``tests/test_qt_overlay.py:26`` for the reference.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.buddy_environment import EnvironmentSnapshot, Kind  # noqa: E402
from tokenpal.ui.qt import weather as w  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_sim(
    weather_code: int | None = 0,
    *,
    temperature: float = 70.0,
    unit: str = "F",
    sensitive: bool = False,
    idle_event: str | None = None,
    hour: int = 12,
    sky_rect: QRectF | None = None,
    buddy_rect: QRectF | None = None,
    seed: int = 0,
) -> w.WeatherSim:
    snap = EnvironmentSnapshot(
        weather_data={
            "weather_code": weather_code,
            "temperature": temperature,
            "unit": unit,
        } if weather_code is not None else None,
        idle_event=idle_event,
        sensitive_suppressed=sensitive,
    )
    sky = sky_rect or QRectF(2000.0, 20.0, 260.0, 200.0)

    return w.WeatherSim(
        env_provider=lambda: snap,
        sky_rect_provider=lambda: sky,
        buddy_rect_provider=lambda: buddy_rect,
        cell_px=10.0,
        rng=random.Random(seed),
        now_hour=lambda: hour,
    )


def _run(sim: w.WeatherSim, seconds: float, step: float = 0.05) -> None:
    t = 0.0
    while t < seconds:
        sim.tick(step)
        t += step


# --- Env wiring ----------------------------------------------------------


def test_env_derived_from_snapshot_day() -> None:
    sim = _make_sim(weather_code=0, hour=12)
    sim.tick(0.1)
    assert sim.env is not None
    assert sim.env.kind is Kind.CLEAR
    assert sim.env.is_day is True


def test_env_night_hour() -> None:
    sim = _make_sim(weather_code=0, hour=22)
    sim.tick(0.1)
    assert sim.env is not None
    assert sim.env.is_day is False


def test_env_no_snapshot_returns_clear_default() -> None:
    sim = w.WeatherSim(
        env_provider=lambda: None,
        sky_rect_provider=lambda: QRectF(0, 0, 260, 200),
        cell_px=10.0,
        rng=random.Random(0),
        now_hour=lambda: 12,
    )
    sim.tick(0.1)
    assert sim.env is not None
    assert sim.env.kind is Kind.CLEAR


# --- Spawn-rate monotonicity --------------------------------------------


def test_rain_spawn_monotonic_in_intensity() -> None:
    # WMO 61 → RAIN 0.3, WMO 65 → RAIN 1.0 (ascii per buddy_environment).
    low = _make_sim(weather_code=61, seed=1)
    high = _make_sim(weather_code=65, seed=1)
    _run(low, 3.0)
    _run(high, 3.0)
    low_rain = sum(1 for p in low.particles if p.kind == "rain")
    high_rain = sum(1 for p in high.particles if p.kind == "rain")
    assert high_rain > low_rain, (low_rain, high_rain)


def test_snow_spawn_monotonic_in_intensity() -> None:
    # WMO 71 → SNOW 0.3, WMO 75 → SNOW 1.0.
    low = _make_sim(weather_code=71, seed=2)
    high = _make_sim(weather_code=75, seed=2)
    _run(low, 3.0)
    _run(high, 3.0)
    low_n = sum(1 for p in low.particles if p.kind == "snow")
    high_n = sum(1 for p in high.particles if p.kind == "snow")
    assert high_n > low_n, (low_n, high_n)


def test_clear_day_spawns_no_particles() -> None:
    sim = _make_sim(weather_code=0, hour=12, seed=3)
    _run(sim, 2.0)
    assert sim.particles == []


# --- Lightning bounded ---------------------------------------------------


def test_lightning_duty_cycle_bounded() -> None:
    # WMO 99 → STORM 1.0, max spawn pressure. Count flash triggers in 60 s
    # of sim time and assert it never exceeds the hard min-gap.
    sim = _make_sim(weather_code=99, seed=4)
    triggers = 0
    was_active = False
    for _ in range(int(60.0 / 0.05)):
        sim.tick(0.05)
        if sim.lightning.active and not was_active:
            triggers += 1
        was_active = sim.lightning.active
    # With _LIGHTNING_MIN_GAP_S=8s → max 8 triggers in 60s (plus the first).
    assert triggers <= 9, triggers


def test_lightning_only_in_storm() -> None:
    sim = _make_sim(weather_code=65, seed=4)  # rain, not storm
    _run(sim, 30.0)
    assert sim.lightning.active is False


# --- Shooting star -------------------------------------------------------


def test_shooting_star_only_clear_night() -> None:
    # Rain night → no shooting stars.
    sim = _make_sim(weather_code=65, hour=23, seed=5)
    _run(sim, 60.0)
    assert sim.shooting_stars == []


def test_shooting_star_fires_on_clear_night() -> None:
    sim = _make_sim(weather_code=0, hour=23, seed=5)
    # _SHOOTING_STAR_MAX_S is 120 s — run 180 to guarantee ≥1 at typical seeds.
    _run(sim, 180.0, step=0.1)
    # At least one shooting star must have fired in 180s (either currently
    # active or already expired). Track via a spawn counter hack: re-run
    # counting distinct ShootingStar identities that ever existed.
    # Simpler: assert the scheduler decremented below zero at least once.
    # Rerun with patched _spawn_shooting_star.
    seen = []
    sim2 = _make_sim(weather_code=0, hour=23, seed=5)
    original = sim2._spawn_shooting_star

    def tracked() -> None:
        seen.append(1)
        original()

    sim2._spawn_shooting_star = tracked  # type: ignore[method-assign]
    _run(sim2, 180.0, step=0.1)
    assert len(seen) >= 1


# --- Overcast threshold --------------------------------------------------


def test_overcast_only_at_intensity_threshold(qapp: QApplication) -> None:
    # Partly cloudy (WMO 2 → CLOUDY 0.4) vs overcast (WMO 3 → CLOUDY 0.8).
    partly = _make_sim(weather_code=2, hour=12, seed=6)
    overcast = _make_sim(weather_code=3, hour=12, seed=6)
    partly.tick(0.1)
    overcast.tick(0.1)
    assert partly.env is not None
    assert overcast.env is not None
    # The Kind is the same for both; the threshold is read at paint time.
    # Assert the boundary matches what SkyWindow relies on.
    assert partly.env.kind is Kind.CLOUDY
    assert partly.env.intensity < w._OVERCAST_INTENSITY
    assert overcast.env.kind is Kind.CLOUDY
    assert overcast.env.intensity >= w._OVERCAST_INTENSITY


# --- Sensitive-suppressed CLEAR (intentional divergence from Textual) ---


def test_sensitive_suppressed_clears_state() -> None:
    # Rain sim runs for a bit so state accumulates, then the flag flips.
    snap_on = EnvironmentSnapshot(
        weather_data={"weather_code": 65, "temperature": 70.0, "unit": "F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    snap_suppressed = EnvironmentSnapshot(
        weather_data={"weather_code": 65, "temperature": 70.0, "unit": "F"},
        idle_event=None,
        sensitive_suppressed=True,
    )
    state = {"snap": snap_on}
    sim = w.WeatherSim(
        env_provider=lambda: state["snap"],
        sky_rect_provider=lambda: QRectF(2000.0, 20.0, 260.0, 200.0),
        cell_px=10.0,
        rng=random.Random(7),
        now_hour=lambda: 12,
    )
    _run(sim, 3.0)
    assert len(sim.particles) > 0
    state["snap"] = snap_suppressed
    sim.tick(0.1)
    # Divergence from Textual: we clear, we don't freeze.
    assert sim.particles == []
    assert sim.shooting_stars == []
    assert sim.lightning.active is False


def test_sensitive_resume_can_respawn() -> None:
    snap_off = EnvironmentSnapshot(
        weather_data={"weather_code": 65, "temperature": 70.0, "unit": "F"},
        idle_event=None,
        sensitive_suppressed=False,
    )
    snap_on = EnvironmentSnapshot(
        weather_data={"weather_code": 65, "temperature": 70.0, "unit": "F"},
        idle_event=None,
        sensitive_suppressed=True,
    )
    state = {"snap": snap_on}
    sim = w.WeatherSim(
        env_provider=lambda: state["snap"],
        sky_rect_provider=lambda: QRectF(2000.0, 20.0, 260.0, 200.0),
        cell_px=10.0,
        rng=random.Random(8),
        now_hour=lambda: 12,
    )
    _run(sim, 1.0)
    assert sim.particles == []
    state["snap"] = snap_off
    _run(sim, 2.0)
    assert len(sim.particles) > 0


# --- Buddy contact / splash ---------------------------------------------


def test_rain_contact_spawns_splash() -> None:
    # Buddy rect sits directly below the cloud so rain drops that hit it
    # spawn splash particles.
    sky = QRectF(2000.0, 20.0, 260.0, 200.0)
    buddy = QRectF(
        sky.left(), sky.bottom() + 20.0, sky.width(), 100.0,
    )
    sim = _make_sim(
        weather_code=65, sky_rect=sky, buddy_rect=buddy, seed=11,
    )
    _run(sim, 4.0)
    splashes = [p for p in sim.particles if p.kind == "splash"]
    # With 4 s of rain at intensity 1.0, multiple drops should have landed.
    assert len(splashes) > 0


def test_clear_buddy_accum_removes_snow_dust() -> None:
    sim = _make_sim(weather_code=0, seed=12)
    # Inject a fake snow-dust particle and make sure clear_buddy_accum
    # wipes it without touching other particles.
    sim.particles.append(w.WeatherParticle(
        kind="snow_dust", x=0, y=0, vx=0, vy=0, life=10.0, glyph="·",
        color=w._COL_SNOW,
    ))
    sim.particles.append(w.WeatherParticle(
        kind="rain", x=0, y=0, vx=0, vy=0, life=10.0, glyph=".",
        color=w._COL_RAIN,
    ))
    sim.clear_buddy_accum()
    assert [p.kind for p in sim.particles] == ["rain"]


# --- Widget smoke (headless) --------------------------------------------


def test_sky_window_constructs_and_hidden_default(qapp: QApplication) -> None:
    sim = _make_sim(weather_code=0, seed=13)
    sky = w.SkyWindow(sim)
    # Assert the user-intent flag (isHidden), not isVisible — translucent
    # frameless windows lie on macOS between event pumps.
    assert sky.isHidden() is True


def test_buddy_overlay_hides_without_rect(qapp: QApplication) -> None:
    sim = _make_sim(weather_code=0, seed=14)
    overlay = w.BuddyRainOverlay(sim, buddy_rect_provider=lambda: None)
    overlay.show()
    overlay.reanchor()
    assert overlay.isHidden() is True


def test_buddy_overlay_reanchor_idempotent(qapp: QApplication) -> None:
    sim = _make_sim(weather_code=0, seed=15)
    rect = QRectF(500.0, 500.0, 120.0, 160.0)
    overlay = w.BuddyRainOverlay(sim, buddy_rect_provider=lambda: rect)
    overlay.reanchor()
    first = overlay.pos()
    overlay.reanchor()
    second = overlay.pos()
    assert (first.x(), first.y()) == (second.x(), second.y())


# --- Sprite pixmap cache -------------------------------------------------


def test_sprite_pixmap_cache_hits_on_second_call(qapp: QApplication) -> None:
    """Repeated requests for the same sprite + color + cell size must
    return the cached QPixmap, not rebuild it. Without the cache, every
    paint would supersample-render and downsample anew."""
    from PySide6.QtGui import QColor

    from tokenpal.ui.ascii_props import SUN_SPRITE
    sim = _make_sim(weather_code=0, seed=20)
    sky = w.SkyWindow(sim)
    color = QColor("#ffcc44")
    pix1 = sky._sprite_pixmap(SUN_SPRITE, color)
    pix2 = sky._sprite_pixmap(SUN_SPRITE, color)
    assert pix1 is pix2
    assert len(sky._sprite_cache) == 1


def test_sprite_pixmap_cache_busted_on_zoom(qapp: QApplication) -> None:
    """``set_zoom`` recomputes cell metrics and must clear the cache so
    the next paint rebuilds at the new size."""
    from PySide6.QtGui import QColor

    from tokenpal.ui.ascii_props import SUN_SPRITE
    sim = _make_sim(weather_code=0, seed=21)
    sky = w.SkyWindow(sim)
    sky._sprite_pixmap(SUN_SPRITE, QColor("#ffcc44"))
    assert len(sky._sprite_cache) == 1
    sky.set_zoom(1.5)
    assert len(sky._sprite_cache) == 0


def test_set_zoom_no_op_on_same_factor(qapp: QApplication) -> None:
    """Calling set_zoom with the current factor must not bust the cache
    (avoids needless rebuilds when the orchestrator fans out a no-op)."""
    from PySide6.QtGui import QColor

    from tokenpal.ui.ascii_props import SUN_SPRITE
    sim = _make_sim(weather_code=0, seed=22)
    sky = w.SkyWindow(sim)
    sky._sprite_pixmap(SUN_SPRITE, QColor("#ffcc44"))
    sky.set_zoom(1.0)
    assert len(sky._sprite_cache) == 1

"""Tests for the productivity sense bucket-gating behavior."""

from __future__ import annotations

from tokenpal.senses.productivity.memory_stats import ProductivityStats


def _stats(
    *, time_in_current_min: int = 5, switches_per_hour: float = 3.0,
    longest_streak_min: int = 0, current_app: str = "Terminal",
    session_minutes: int = 10, total_switches: int = 1,
) -> dict:
    return {
        "current_app": current_app,
        "category": "terminal",
        "time_in_current_min": time_in_current_min,
        "switches_per_hour": switches_per_hour,
        "longest_streak_min": longest_streak_min,
        "total_switches": total_switches,
        "session_minutes": session_minutes,
    }


def test_bucket_key_switch_tiers() -> None:
    assert ProductivityStats._bucket_key(_stats(switches_per_hour=3))[1] == "calm"
    assert ProductivityStats._bucket_key(_stats(switches_per_hour=10))[1] == "active"
    assert ProductivityStats._bucket_key(_stats(switches_per_hour=20))[1] == "restless"


def test_bucket_key_time_tiers() -> None:
    assert ProductivityStats._bucket_key(_stats(time_in_current_min=2))[0] == "just_arrived"
    assert ProductivityStats._bucket_key(_stats(time_in_current_min=15))[0] == "settled"
    assert ProductivityStats._bucket_key(_stats(time_in_current_min=35))[0] == "deep_focus"


def test_bucket_key_streak_tier() -> None:
    assert ProductivityStats._bucket_key(_stats(longest_streak_min=10))[2] == "none"
    assert ProductivityStats._bucket_key(_stats(longest_streak_min=45))[2] == "long_streak"


def test_summary_omits_drifting_integers() -> None:
    """Integer drift within a bucket must not change the summary string."""
    sense = ProductivityStats({})
    s1 = sense._build_summary(_stats(time_in_current_min=12, switches_per_hour=9))
    s2 = sense._build_summary(_stats(time_in_current_min=14, switches_per_hour=11))
    assert s1 == s2, f"summary changed within bucket: {s1!r} vs {s2!r}"


def test_summary_changes_across_buckets() -> None:
    sense = ProductivityStats({})
    calm = sense._build_summary(_stats(switches_per_hour=3))
    restless = sense._build_summary(_stats(switches_per_hour=20))
    assert calm != restless

"""Unit tests for the FollowupSession dataclass + TTL / cap helpers."""

from __future__ import annotations

import time

from tokenpal.brain.research import Source
from tokenpal.brain.research_followup import (
    FollowupSession,
    bump,
    is_expired,
    over_cap,
    touch,
)


def _make_session(**overrides):
    defaults = dict(
        mode="synth",
        model="claude-haiku-4-5",
        sources=[Source(number=1, url="https://example.com", title="x", excerpt="y")],
        messages=[
            {"role": "user", "content": "prompt text"},
            {"role": "assistant", "content": "answer text"},
        ],
        tools=[],
        ttl_s=900,
        max_followups=5,
    )
    defaults.update(overrides)
    return FollowupSession(**defaults)


def test_fresh_session_not_expired_or_over_cap() -> None:
    s = _make_session()
    assert not is_expired(s)
    assert not over_cap(s)
    assert s.followup_count == 0


def test_is_expired_true_past_ttl() -> None:
    s = _make_session(ttl_s=10)
    # Pretend last use was 30s ago.
    s.last_used_at = time.time() - 30
    assert is_expired(s)


def test_is_expired_false_within_ttl() -> None:
    s = _make_session(ttl_s=900)
    s.last_used_at = time.time() - 60
    assert not is_expired(s)


def test_bump_increments_and_updates_last_used() -> None:
    s = _make_session()
    before = s.last_used_at
    time.sleep(0.01)
    bump(s)
    assert s.followup_count == 1
    assert s.last_used_at > before


def test_touch_updates_last_used_without_incrementing() -> None:
    s = _make_session()
    before = s.last_used_at
    time.sleep(0.01)
    touch(s)
    assert s.followup_count == 0
    assert s.last_used_at > before


def test_over_cap_true_at_limit() -> None:
    s = _make_session(max_followups=3)
    for _ in range(3):
        bump(s)
    assert over_cap(s)


def test_over_cap_false_before_limit() -> None:
    s = _make_session(max_followups=3)
    bump(s)
    bump(s)
    assert not over_cap(s)


def test_sliding_window_not_applied() -> None:
    """TTL is measured from last_used_at, but calls don't extend it beyond
    ttl_s — the spec in smarter-buddy.md states "no sliding-window renewal"
    for cost-bounding reasons.

    Bumping DOES update last_used_at (legitimate use resets the window).
    Just asserting that is_expired properly keys off last_used_at not
    created_at.
    """
    s = _make_session(ttl_s=10)
    s.created_at = time.time() - 3600  # created an hour ago
    # Recent use — not expired.
    touch(s)
    assert not is_expired(s)
    # Old use — expired, regardless of created_at.
    s.last_used_at = time.time() - 100
    assert is_expired(s)


def test_mode_literal_accepts_three_modes() -> None:
    for mode in ("synth", "search", "deep"):
        s = _make_session(mode=mode)
        assert s.mode == mode

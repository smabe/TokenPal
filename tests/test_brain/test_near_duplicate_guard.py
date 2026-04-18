"""Tests for the orchestrator's near-duplicate output guard."""

from __future__ import annotations

from collections import deque

from tokenpal.brain.orchestrator import (
    _NEAR_DUPLICATE_JACCARD,
    _PREFIX_LOCK_MIN_MATCHES,
    Brain,
)


def test_trigram_set_basic() -> None:
    ts = Brain._trigram_set("hello world")
    assert "hel" in ts
    assert "ell" in ts
    # Case-insensitive.
    assert Brain._trigram_set("HELLO") == Brain._trigram_set("hello")


def test_trigram_set_strips_punctuation() -> None:
    assert Brain._trigram_set("hi!!!") == Brain._trigram_set("hi")


def _make_harness() -> Brain:
    """Bare orchestrator instance that exposes just the dedupe ring + helper."""
    obj = Brain.__new__(Brain)
    obj._recent_outputs = deque(maxlen=10)
    return obj


def test_exact_duplicate_rejected() -> None:
    o = _make_harness()
    o._recent_outputs.append("Jake, 64 switches an hour, you hyperactive squirrel!")
    assert o._is_near_duplicate("Jake, 64 switches an hour, you hyperactive squirrel!")


def test_drifting_integer_still_near_duplicate() -> None:
    o = _make_harness()
    o._recent_outputs.append("Bro, 64 switches per hour — hyperactive squirrel vibes!")
    assert o._is_near_duplicate(
        "Bro, 63 switches per hour — hyperactive squirrel vibes!"
    )


def test_unrelated_line_passes() -> None:
    o = _make_harness()
    o._recent_outputs.append("Jake, you've been in Xcode for twenty minutes, nice.")
    assert not o._is_near_duplicate(
        "The weather outside looks absolutely dreadful, Finn."
    )


def test_empty_ring_never_duplicate() -> None:
    o = _make_harness()
    assert not o._is_near_duplicate("anything at all goes here")


def test_prefix_lock_catches_template_drift() -> None:
    """The Finn 'Jake, good cop...' lock-in pattern: same lead, different tail."""
    o = _make_harness()
    o._recent_outputs.append("Jake, good cop... this keyboard's got more dirt than a dungeon!")
    o._recent_outputs.append("Jake, good cop... this commit's got more letters than a wizard!")
    o._recent_outputs.append("Jake, good cop... midnight's creepin' in, bro!")
    # Fourth time, same lead — should be suppressed.
    assert o._is_near_duplicate(
        "Jake, good cop... this app's got more bugs than a witch's hat!"
    )


def test_prefix_lock_leaves_varied_leads_alone() -> None:
    """Two copies of the same lead is fine, three is the drift bar."""
    o = _make_harness()
    o._recent_outputs.append("Jake, good cop... this keyboard's slow!")
    o._recent_outputs.append("Jake, good cop... this commit's weird!")
    # Only 2 matches; still below the threshold.
    assert not o._is_near_duplicate(
        "The weather outside is absolutely dreadful, Finn."
    )


def test_prefix_lock_threshold_constant_is_sane() -> None:
    assert 2 <= _PREFIX_LOCK_MIN_MATCHES <= 5


def test_threshold_constant_is_sane() -> None:
    assert 0.5 <= _NEAR_DUPLICATE_JACCARD <= 0.9

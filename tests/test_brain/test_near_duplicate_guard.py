"""Tests for the orchestrator's near-duplicate output guard."""

from __future__ import annotations

from collections import deque

from tokenpal.brain.orchestrator import _NEAR_DUPLICATE_JACCARD, Brain


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
    obj._recent_outputs = deque(maxlen=5)
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


def test_threshold_constant_is_sane() -> None:
    assert 0.5 <= _NEAR_DUPLICATE_JACCARD <= 0.9

"""Tests for RunningBit infrastructure on PersonalityEngine."""

from __future__ import annotations

import time

from tokenpal.brain.personality import PersonalityEngine, RunningBit


def _engine() -> PersonalityEngine:
    return PersonalityEngine(persona_prompt="You are a test bot.")


def test_add_running_bit_registers_and_renders_in_prompt() -> None:
    eng = _engine()
    eng.add_running_bit(
        tag="morning_word",
        framing="Today's word: oxymoron. Slip it in naturally once.",
        decay_s=3600,
    )
    prompt = eng.build_prompt(context_snapshot="App: Ghostty")
    assert "Running bits you can organically weave in today" in prompt
    assert "oxymoron" in prompt


def test_running_bit_appears_in_freeform_prompt_too() -> None:
    eng = _engine()
    eng.add_running_bit(
        tag="todays_joke_bit",
        framing="You heard a joke about eyebrows. Callback only.",
        decay_s=3600,
    )
    prompt = eng.build_freeform_prompt()
    assert "eyebrows" in prompt


def test_expired_bits_pruned_from_prompt() -> None:
    eng = _engine()
    eng.add_running_bit(tag="expired", framing="old news", decay_s=3600)
    # Backdate the bit so decay_at has already passed.
    eng._running_bits[0] = RunningBit(
        tag="expired",
        framing="old news",
        added_at=time.monotonic() - 7200,
        decay_at=time.monotonic() - 3600,
    )
    prompt = eng.build_prompt(context_snapshot="")
    assert "old news" not in prompt
    assert eng.active_running_bits() == []


def test_same_tag_replaces_in_place() -> None:
    eng = _engine()
    eng.add_running_bit(tag="morning_word", framing="first word", decay_s=3600)
    eng.add_running_bit(tag="morning_word", framing="second word", decay_s=3600)
    bits = eng.active_running_bits()
    assert len(bits) == 1
    assert bits[0].framing == "second word"


def test_lru_eviction_at_max_three() -> None:
    eng = _engine()
    eng.add_running_bit(tag="a", framing="A", decay_s=3600)
    # Stagger added_at so LRU ordering is deterministic.
    eng._running_bits[-1].added_at -= 300
    eng.add_running_bit(tag="b", framing="B", decay_s=3600)
    eng._running_bits[-1].added_at -= 200
    eng.add_running_bit(tag="c", framing="C", decay_s=3600)
    eng._running_bits[-1].added_at -= 100
    # Fourth bit should evict oldest ("a").
    eng.add_running_bit(tag="d", framing="D", decay_s=3600)
    tags = [b.tag for b in eng.active_running_bits()]
    assert "a" not in tags
    assert set(tags) == {"b", "c", "d"}


def test_no_bits_means_no_running_bits_header() -> None:
    eng = _engine()
    prompt = eng.build_prompt(context_snapshot="")
    assert "Running bits you can organically weave in" not in prompt


def test_payload_is_stored_on_bit() -> None:
    eng = _engine()
    eng.add_running_bit(
        tag="morning_word",
        framing="Today's word: oxymoron.",
        decay_s=3600,
        payload={"output": "oxymoron: contradictory"},
    )
    bit = eng.active_running_bits()[0]
    assert bit.payload == {"output": "oxymoron: contradictory"}

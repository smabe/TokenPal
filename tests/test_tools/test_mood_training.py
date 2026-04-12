"""Tests for custom mood training prompt parsing and VoiceProfile mood fields."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tokenpal.tools.train_voice import _parse_custom_moods
from tokenpal.tools.voice_profile import (
    VoiceProfile,
    load_profile,
    make_profile,
    save_profile,
)


# ---------------------------------------------------------------
# _parse_custom_moods
# ---------------------------------------------------------------

_VALID_INPUT = """\
DEFAULT | PLAYFUL | BMO is feeling playful boyee!
SLEEPY | DROWSY | BMO cannot self-snooze.
BORED | BLAH | Nothing is happening boy.
HYPER | TURBO | Everything is awesome!
IMPRESSED | WHOA | That was algebraic.
CONCERNED | WORRIED | Are you okay boy?"""


def test_parse_valid_input():
    result = _parse_custom_moods(_VALID_INPUT)
    assert result is not None
    prompts, roles, default_mood = result
    assert len(prompts) == 6
    assert len(roles) == 6
    assert default_mood == "PLAYFUL"
    assert roles["bored"] == "BLAH"
    assert "TURBO" in prompts["hyper"]


def test_parse_with_extra_whitespace():
    text = "  DEFAULT | PLAYFUL | desc.\n  SLEEPY | DROWSY | desc.\n  BORED | BLAH | desc.\n  HYPER | TURBO | desc.\n  IMPRESSED | WHOA | desc.\n  CONCERNED | WORRIED | desc."
    result = _parse_custom_moods(text)
    assert result is not None


def test_parse_with_preamble_lines():
    text = "Here are the moods:\n\n" + _VALID_INPUT + "\n\nHope that helps!"
    result = _parse_custom_moods(text)
    assert result is not None


def test_parse_rejects_too_few_moods():
    text = "DEFAULT | PLAYFUL | desc.\nSLEEPY | DROWSY | desc."
    assert _parse_custom_moods(text) is None


def test_parse_rejects_duplicate_names():
    text = _VALID_INPUT.replace("BLAH", "PLAYFUL")  # BORED gets same name as DEFAULT
    assert _parse_custom_moods(text) is None


def test_parse_rejects_multiword_names():
    text = _VALID_INPUT.replace("PLAYFUL", "VERY PLAYFUL")
    assert _parse_custom_moods(text) is None


def test_parse_allows_hyphenated_names():
    text = _VALID_INPUT.replace("PLAYFUL", "OVER-IT")
    result = _parse_custom_moods(text)
    assert result is not None
    assert result[1]["default"] == "OVER-IT"


def test_parse_adds_period_when_missing():
    text = _VALID_INPUT.replace("BMO is feeling playful boyee!", "BMO is playful")
    result = _parse_custom_moods(text)
    assert result is not None
    assert result[0]["default"].endswith(".")


def test_parse_no_double_period():
    result = _parse_custom_moods(_VALID_INPUT)
    assert result is not None
    for prompt in result[0].values():
        assert ".." not in prompt
        assert "!." not in prompt
        assert "?." not in prompt


# ---------------------------------------------------------------
# VoiceProfile mood_roles / default_mood round-trip
# ---------------------------------------------------------------

def test_save_load_roundtrip_with_mood_roles():
    profile = make_profile(
        character="BMO",
        source="test",
        lines=["Line 1", "Line 2"],
        mood_roles={"default": "PLAYFUL", "bored": "BLAH"},
        default_mood="PLAYFUL",
    )
    with tempfile.TemporaryDirectory() as td:
        voices_dir = Path(td)
        save_profile(profile, voices_dir)
        loaded = load_profile("bmo", voices_dir)
    assert loaded.mood_roles == {"default": "PLAYFUL", "bored": "BLAH"}
    assert loaded.default_mood == "PLAYFUL"


def test_load_profile_without_mood_roles():
    """Old profiles on disk lack mood_roles/default_mood — should load fine."""
    old_data = {
        "character": "Bender",
        "source": "test",
        "created": "2026-01-01",
        "lines": ["Line 1"],
        "mood_prompts": {"snarky": "Your current mood: SNARKY. Bite me."},
        "version": 1,
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "bender.json"
        path.write_text(json.dumps(old_data), encoding="utf-8")
        loaded = load_profile("bender", Path(td))
    assert loaded.mood_roles == {}
    assert loaded.default_mood == ""
    assert loaded.mood_prompts == {"snarky": "Your current mood: SNARKY. Bite me."}

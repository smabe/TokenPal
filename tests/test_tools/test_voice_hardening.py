"""Tests for voice-generation drift guards, audit, and full regenerate."""

from __future__ import annotations

from unittest.mock import patch

from tokenpal.tools import train_voice
from tokenpal.tools.train_voice import (
    _frames_look_usable,
    _parse_numbered_lines,
    _validate_persona,
    audit_profile,
)
from tokenpal.tools.voice_profile import VoiceProfile, save_profile
from tokenpal.util.text_guards import is_clean_english

# ---------------------------------------------------------------
# is_clean_english
# ---------------------------------------------------------------


class TestCleanEnglish:
    def test_accepts_plain_english(self):
        assert is_clean_english("Hey man, whoa!")
        assert is_clean_english("Respond with excited slang.")

    def test_rejects_empty(self):
        assert not is_clean_english("")
        assert not is_clean_english("   ")

    def test_rejects_thai(self):
        assert not is_clean_english("ยังไงก็อยู่ในกลุ่ม")

    def test_rejects_chinese(self):
        assert not is_clean_english("銀行家，我只想平静地活下去。")

    def test_rejects_german_meta(self):
        assert not is_clean_english(
            "copiert/paste von Wikipedia/nachweislich kopiert.",
        )

    def test_rejects_markdown_headers(self):
        assert not is_clean_english("**Analyze the German/Formatting Parts:**")
        assert not is_clean_english("**expansion:**")

    def test_rejects_chain_of_thought(self):
        assert not is_clean_english(
            "I cannot provide a definitive answer without more context.",
        )
        assert not is_clean_english("If the goal is to respond neutrally...")

    def test_allows_mild_accents(self):
        # "café" should pass - single non-ASCII char well under threshold
        assert is_clean_english("Grab a café before the meeting, man.")


# ---------------------------------------------------------------
# _validate_persona
# ---------------------------------------------------------------


class TestValidatePersona:
    def test_accepts_full_card(self):
        text = (
            "VOICE: You yell constantly.\n"
            'CATCHPHRASES: "whoa", "man"\n'
            "NEVER: Speak softly.\n"
            "WORLDVIEW: Heroes win."
        )
        assert _validate_persona(text)

    def test_rejects_missing_section(self):
        assert not _validate_persona("VOICE: you yell")
        assert not _validate_persona("CATCHPHRASES: 'hi'")

    def test_rejects_non_english(self):
        assert not _validate_persona(
            "VOICE: you yell\nCATCHPHRASES: 'hi'\n銀行家，我只想"
            "平静地活下去銀行家，我只想平静地活下去",
        )


# ---------------------------------------------------------------
# _parse_numbered_lines
# ---------------------------------------------------------------


class TestParseNumberedLines:
    def test_strips_numbering(self):
        text = "1. Hey dude!\n2) What's up man\n3. whoa check it"
        out = _parse_numbered_lines(text)
        assert out == ["Hey dude!", "What's up man", "whoa check it"]

    def test_filters_foreign_lines(self):
        text = "1. Hey dude!\n2. ยังไงก็อยู่ในกลุ่มของ 23.\n3. Whoa man"
        out = _parse_numbered_lines(text)
        assert out == ["Hey dude!", "Whoa man"]

    def test_filters_meta_lines(self):
        text = (
            "1. Hey dude!\n"
            "2. **Analyze the German Parts:**\n"
            "3. copiert/paste von Wikipedia\n"
            "4. Whoa man"
        )
        out = _parse_numbered_lines(text)
        assert out == ["Hey dude!", "Whoa man"]

    def test_length_bounds(self):
        short = "1. hi"  # < 8 chars
        long = "1. " + "x" * 70
        out = _parse_numbered_lines(short + "\n" + long)
        assert out == []


# ---------------------------------------------------------------
# _frames_look_usable
# ---------------------------------------------------------------


class TestFramesUsable:
    def test_accepts_full_frames(self):
        frame = [
            "[#ff6600]head[/]",
            "[#ff6600]face[/]",
            "[#00ccff]torso[/]",
            "[#00ccff]legs[/]",
            "[#ffffff]feet[/]",
        ]
        assert _frames_look_usable(frame, frame, frame)

    def test_rejects_all_blank(self):
        assert not _frames_look_usable([""] * 10, [""] * 10, [""] * 10)

    def test_rejects_one_short(self):
        good = ["[#ff6600]x[/]", "[#00ccff]y[/]", "[#ffffff]z[/]", "[#111111]w[/]"]
        bad = [""] * 10
        assert not _frames_look_usable(good, good, bad)

    def test_rejects_monotone(self):
        mono = ["[#55ff55]│││││││││[/]"] * 10
        assert not _frames_look_usable(mono, mono, mono)


# ---------------------------------------------------------------
# audit_profile
# ---------------------------------------------------------------


def _blank_profile(**overrides) -> VoiceProfile:
    base = dict(
        character="Finn",
        source="adventuretime.fandom.com",
        created="2026-04-15T00:00:00",
        lines=["Hey man!", "Whoa!"],
        persona="VOICE: you yell\nCATCHPHRASES: \"whoa\"",
        greetings=["Hey hey!", "What's up?"],
        offline_quips=["Uhh... what?", "My brain hurts."],
        mood_prompts={"default": "Your current mood: HEROIC. Be brave."},
        mood_roles={"default": "HEROIC"},
        default_mood="HEROIC",
        structure_hints=["Respond heroically."],
        ascii_idle=[
            "[#ff6600]head[/]",
            "[#00ccff]torso[/]",
            "[#ffffff]legs[/]",
            "[#111111]feet[/]",
        ],
        ascii_idle_alt=[
            "[#ff6600]head[/]",
            "[#00ccff]torso[/]",
            "[#ffffff]legs[/]",
            "[#111111]feet[/]",
        ],
        ascii_talking=[
            "[#ff6600]head[/]",
            "[#00ccff]torso[/]",
            "[#ffffff]legs[/]",
            "[#111111]feet[/]",
        ],
    )
    base.update(overrides)
    return VoiceProfile(**base)


class TestAudit:
    def test_clean_profile_has_no_issues(self):
        report = audit_profile(_blank_profile())
        assert report.ok, report.issues

    def test_flags_non_english_greetings(self):
        p = _blank_profile(greetings=[
            "copiert/paste von Wikipedia/nachweislich kopiert.",
            "**Analyze:**",
        ])
        report = audit_profile(p)
        assert any("greetings" in i for i in report.issues)

    def test_flags_empty_moods(self):
        p = _blank_profile(
            mood_prompts={}, mood_roles={}, default_mood="",
        )
        report = audit_profile(p)
        assert any("mood_prompts" in i for i in report.issues)
        assert any("default_mood" in i for i in report.issues)

    def test_flags_blank_ascii(self):
        p = _blank_profile(
            ascii_idle=[""] * 10,
            ascii_idle_alt=[""] * 10,
            ascii_talking=[""] * 10,
        )
        report = audit_profile(p)
        assert any("ascii" in i for i in report.issues)

    def test_flags_bad_persona(self):
        p = _blank_profile(persona="I cannot provide a definitive answer.")
        report = audit_profile(p)
        assert any("persona" in i for i in report.issues)


# ---------------------------------------------------------------
# regenerate_voice_assets — full refresh including greetings/moods
# ---------------------------------------------------------------


def test_regenerate_refreshes_all_llm_fields(tmp_path):
    """After regen, greetings/moods/hints must be fresh, not preserved."""
    broken = _blank_profile(
        greetings=["copiert/paste"],
        offline_quips=["**Analyze:**"],
        mood_prompts={},
        mood_roles={},
        default_mood="",
        structure_hints=[],
    )
    save_profile(broken, tmp_path)

    fake_persona = (
        'VOICE: You yell.\nCATCHPHRASES: "whoa"\nNEVER: whisper\n'
        "WORLDVIEW: heroism"
    )
    good_frame = [
        "[#ff6600]head[/]",
        "[#00ccff]torso[/]",
        "[#ffffff]legs[/]",
        "[#111111]feet[/]",
    ]
    fake_frames = (good_frame, good_frame, good_frame, {})

    with patch.object(
        train_voice, "_generate_persona", return_value=fake_persona,
    ), patch.object(
        train_voice, "_generate_greetings",
        return_value=["Hey now!", "What's up!"],
    ), patch.object(
        train_voice, "_generate_offline_quips",
        return_value=["Uhh.", "My brain."],
    ), patch.object(
        train_voice, "_generate_mood_prompts",
        return_value=(
            {"default": "Your current mood: HEROIC. Be brave."},
            {"default": "HEROIC"},
            "HEROIC",
        ),
    ), patch.object(
        train_voice, "_generate_structure_hints",
        return_value=["Respond heroically."],
    ), patch.object(
        train_voice, "_generate_ascii_art", return_value=fake_frames,
    ):
        result = train_voice.regenerate_voice_assets(broken, tmp_path)

    assert result.greetings == ["Hey now!", "What's up!"]
    assert result.offline_quips == ["Uhh.", "My brain."]
    assert result.default_mood == "HEROIC"
    assert result.structure_hints == ["Respond heroically."]
    assert audit_profile(result).ok


# ---------------------------------------------------------------
# _generate_lines_from_prompt retry loop
# ---------------------------------------------------------------


def test_generate_lines_retries_on_drift():
    """If the first Ollama call returns drift, retry should still produce
    usable lines rather than returning an empty list."""
    calls = {"n": 0}

    def fake_ollama(_prompt, max_tokens=200, temperature=0.9):
        calls["n"] += 1
        if calls["n"] == 1:
            return "1. ยังไงก็อยู่ในกลุ่ม\n2. **Analyze:**"
        return "1. Hey man!\n2. Whoa dude.\n3. What's up!"

    with patch.object(train_voice, "_ollama_generate", side_effect=fake_ollama):
        out = train_voice._generate_lines_from_prompt(
            "Finn", ["line one", "line two"], "Write some greetings.",
        )
    assert out == ["Hey man!", "Whoa dude.", "What's up!"]
    assert calls["n"] == 2


def test_generate_lines_returns_best_effort_after_all_fail():
    """Every attempt drifts - return the best partial (possibly empty)."""

    def fake_ollama(*_args, **_kwargs):
        return "1. ยังไงก็อยู่ในกลุ่ม\n2. **Analyze:**"

    with patch.object(train_voice, "_ollama_generate", side_effect=fake_ollama):
        out = train_voice._generate_lines_from_prompt(
            "Finn", ["line one", "line two"], "Write some greetings.",
        )
    assert out == []

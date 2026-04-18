"""Tests for catchphrase rotation under recent-prefix lock.

When a voice's recent comments all share a leading phrase, the next
_voice_reminder() sample should avoid re-priming with the locked
catchphrase. Otherwise the scaffold phrase ('Jake, good cop...')
keeps self-reinforcing across turns until the model can't escape it.
"""

from __future__ import annotations

from tokenpal.brain.personality import PersonalityEngine
from tokenpal.tools.voice_profile import VoiceProfile


def _engine(catchphrases_line: str) -> PersonalityEngine:
    persona = f'VOICE: Short.\n\nCATCHPHRASES: {catchphrases_line}\n\n'
    voice = VoiceProfile(
        character="testvoice",
        source="test",
        created="2026-04-17",
        lines=["sample line " + str(i) for i in range(20)],
        persona=persona,
    )
    return PersonalityEngine(persona_prompt="", voice=voice)


def test_locked_catchphrase_dropped_from_sample() -> None:
    """If 2+ recent comments share a lead, that catchphrase is filtered."""
    eng = _engine(
        '"What the what?", "Oh, no, man!", "Jake, good cop...", "What happened next?!"'
    )
    # Seed two recent comments that both lead with "Jake, good cop..."
    eng.record_comment("Jake, good cop... this keyboard got more dirt than a dungeon!")
    eng.record_comment("Jake, good cop... midnight's creepin' in, bro!")
    # Sample many times; locked catchphrase should never surface.
    locked_seen = 0
    for _ in range(50):
        reminder = eng._voice_reminder()
        if "Jake, good cop" in reminder:
            locked_seen += 1
    assert locked_seen == 0


def test_only_one_echo_does_not_lock() -> None:
    """Single echo of a catchphrase doesn't trip the filter."""
    eng = _engine(
        '"What the what?", "Oh, no, man!", "Jake, good cop...", "What happened next?!"'
    )
    eng.record_comment("Jake, good cop... this keyboard got more dirt!")
    # 50 samples pick 3 of 4 catchphrases → expected Jake appearances ~= 37.
    jake_seen = sum(1 for _ in range(50) if "Jake, good cop" in eng._voice_reminder())
    assert jake_seen > 10


def test_fallback_when_all_catchphrases_locked() -> None:
    """If the filter would empty the pool, fall back to full pool."""
    eng = _engine('"Jake, good cop..."')  # only one catchphrase
    eng.record_comment("Jake, good cop... thing A!")
    eng.record_comment("Jake, good cop... thing B!")
    # Even though it's locked, we have nothing else — must not crash.
    reminder = eng._voice_reminder()
    assert "Jake, good cop" in reminder


def test_phrase_prefix_normalizes_punctuation() -> None:
    eng = _engine('"Hello world"')
    assert eng._phrase_prefix("Jake, GOOD cop... more dirt!") == "jake good cop"
    assert eng._phrase_prefix("Jake good cop   extra") == "jake good cop"
    assert eng._phrase_prefix("") == ""

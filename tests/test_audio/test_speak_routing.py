"""tts.speak routing + sentence-split unit tests.

These don't exercise sounddevice — playback path is covered by manual smoke
since CI has no audio device. The dep-missing branch is what matters here:
ambient toggled on without /voice-io install must be a no-op, not a crash.
"""

from __future__ import annotations

from pathlib import Path

from tokenpal.audio import tts
from tokenpal.audio.pipeline import boot
from tokenpal.config.schema import AudioConfig


def test_sentences_split_on_punctuation() -> None:
    out = tts._sentences("Hi there. How are you? Fine!")
    assert out == ["Hi there.", "How are you?", "Fine!"]


def test_sentences_keep_trailing_fragment() -> None:
    # No terminal punctuation: still one sentence (the LLM doesn't always
    # close a bubble with a period).
    assert tts._sentences("just a thought") == ["just a thought"]


def test_sentences_collapse_repeated_punctuation() -> None:
    # "..." should ride with the preceding clause, not become its own item.
    assert tts._sentences("Hmm... maybe not.") == ["Hmm...", "maybe not."]


def test_sentences_handle_newlines() -> None:
    assert tts._sentences("line one\nline two") == ["line one", "line two"]


async def test_typed_source_is_no_op(tmp_path: Path) -> None:
    cfg = AudioConfig(speak_ambient_enabled=True)
    pipeline = boot(cfg, tmp_path)
    # No exception, no playback path entered. If routing were broken we'd
    # crash on the missing models warmup.
    await tts.speak("hello", source="typed", pipeline=pipeline)


async def test_typed_speaks_when_toggle_enabled(tmp_path: Path) -> None:
    # Toggle on but no models on disk — routing accepts the call, then the
    # missing-deps / missing-models guard returns silently. Same shape as
    # ambient: opt-in lets the source through, environment determines
    # whether audio actually plays.
    cfg = AudioConfig(speak_typed_replies_enabled=True)
    pipeline = boot(cfg, tmp_path)
    await tts.speak("hello", source="typed", pipeline=pipeline)


async def test_ambient_off_skips(tmp_path: Path) -> None:
    cfg = AudioConfig(speak_ambient_enabled=False)
    pipeline = boot(cfg, tmp_path)
    await tts.speak("hello", source="ambient", pipeline=pipeline)


async def test_voice_off_skips(tmp_path: Path) -> None:
    cfg = AudioConfig(voice_conversation_enabled=False)
    pipeline = boot(cfg, tmp_path)
    await tts.speak("hello", source="voice", pipeline=pipeline)

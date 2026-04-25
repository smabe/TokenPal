"""Silero VAD wrapper tests.

The hysteresis logic is the only part worth pinning at the unit level —
the onnx forward pass is opaque. We drive _update_hysteresis directly
with synthetic probabilities, which is the same shape of test the real
onnx scorer would produce minus the model file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpal.audio.vad import (
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    SileroVAD,
    VadEvent,
)


CHUNK_S = CHUNK_SAMPLES / SAMPLE_RATE


def test_models_present_false_on_fresh_dir(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path)
    assert v.models_present() is False


async def test_warmup_raises_when_model_missing(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path)
    with pytest.raises(FileNotFoundError):
        await v.warmup()


def test_process_returns_none_when_not_warm(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path)
    # Even valid-looking PCM is a no-op until warmup() succeeds.
    assert v.process(b"\x00\x00" * CHUNK_SAMPLES) is None


def test_speech_started_after_min_speech_run(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path, threshold=0.5, min_speech_s=0.05)
    # Two 32ms chunks of speech = 64ms — crosses 50ms threshold.
    assert v._update_hysteresis(0.9, CHUNK_S) is None  # 32ms run
    assert v._update_hysteresis(0.9, CHUNK_S) == VadEvent.SPEECH_STARTED
    assert v._in_speech is True


def test_one_loud_frame_is_not_enough(tmp_path: Path) -> None:
    # A single 32ms above-threshold frame shouldn't fire — the min_speech
    # bar is what ignores cough/click false positives.
    v = SileroVAD(tmp_path, threshold=0.5, min_speech_s=0.10)
    assert v._update_hysteresis(0.9, CHUNK_S) is None
    assert v._update_hysteresis(0.1, CHUNK_S) is None  # speech run resets
    assert v._in_speech is False


def test_speech_ended_after_min_silence_run(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path, threshold=0.5, min_speech_s=0.05, min_silence_s=0.7)
    # Get into speech first.
    v._update_hysteresis(0.9, CHUNK_S)
    v._update_hysteresis(0.9, CHUNK_S)
    assert v._in_speech is True

    # 23 chunks of silence = 736ms > 700ms threshold.
    chunks_to_silence = int(0.7 / CHUNK_S) + 1
    last = None
    for _ in range(chunks_to_silence):
        last = v._update_hysteresis(0.05, CHUNK_S)
    assert last == VadEvent.SPEECH_ENDED
    assert v._in_speech is False


def test_brief_silence_in_speech_does_not_end(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path, threshold=0.5, min_speech_s=0.05, min_silence_s=0.7)
    v._update_hysteresis(0.9, CHUNK_S)
    v._update_hysteresis(0.9, CHUNK_S)
    # ~200ms of dip, then loud again — natural mid-sentence pause.
    for _ in range(6):  # 192ms
        assert v._update_hysteresis(0.10, CHUNK_S) is None
    assert v._update_hysteresis(0.9, CHUNK_S) is None
    assert v._in_speech is True


def test_reset_clears_state_and_runs(tmp_path: Path) -> None:
    v = SileroVAD(tmp_path)
    v._in_speech = True
    v._speech_run_s = 99.0
    v._silence_run_s = 99.0
    v.reset()
    assert v._in_speech is False
    assert v._speech_run_s == 0.0
    assert v._silence_run_s == 0.0

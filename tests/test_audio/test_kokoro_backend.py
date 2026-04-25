"""KokoroBackend contract tests.

These cover the surface that doesn't need real model files: namespacing,
file-presence check, voice id parsing. Streaming / warmup against real
weights is left to manual smoke and the --validate audio check, since
pulling 177MB of onnx in CI is the wrong tradeoff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpal.audio.backends.kokoro import (
    MODEL_FILENAMES,
    VOICES_FILENAME,
    KokoroBackend,
)
from tokenpal.audio.registry import get_tts_backend, registered_tts_backends


def test_registered_under_kokoro() -> None:
    # Importing the module is enough — the decorator wires it up.
    assert "kokoro" in registered_tts_backends()
    assert get_tts_backend("kokoro") is KokoroBackend


def test_paths_resolve_per_quantization(tmp_path: Path) -> None:
    b_fp16 = KokoroBackend(tmp_path, quantization="fp16")
    b_int8 = KokoroBackend(tmp_path, quantization="int8")
    b_fp32 = KokoroBackend(tmp_path, quantization="fp32")
    assert b_fp16.model_path.name == MODEL_FILENAMES["fp16"]
    assert b_int8.model_path.name == MODEL_FILENAMES["int8"]
    assert b_fp32.model_path.name == MODEL_FILENAMES["fp32"]
    assert b_fp16.voices_path.name == VOICES_FILENAME


def test_models_present_false_on_fresh_dir(tmp_path: Path) -> None:
    b = KokoroBackend(tmp_path)
    assert b.models_present() is False
    assert b.list_voices() == []


async def test_warmup_raises_when_models_missing(tmp_path: Path) -> None:
    b = KokoroBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await b.warmup()

"""Kokoro-onnx TTS backend.

Default voice for both ambient and voice-conversation paths. Concrete
implementation around https://github.com/thewh1teagle/kokoro-onnx.

Heavy imports (kokoro_onnx, numpy) are kept inside methods so the modularity
test stays green: ambient-only boots that never call ``warmup()`` /
``synthesize()`` won't touch onnxruntime.

Model files live at ``<data_dir>/audio/`` and are fetched by
``tokenpal.audio.deps.install_models()``:

    kokoro-v1.0.onnx       (fp32, ~325MB)
    kokoro-v1.0.fp16.onnx  (~177MB) — quality default
    kokoro-v1.0.int8.onnx  (~92MB)  — auto on ≤8GB RAM
    voices-v1.0.bin        (~28MB)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from tokenpal.audio.base import TTSBackend, VoiceInfo
from tokenpal.audio.registry import register_tts_backend

if TYPE_CHECKING:
    from kokoro_onnx import Kokoro

log = logging.getLogger(__name__)

Quantization = Literal["int8", "fp16", "fp32"]

# Suffix encoded in the GitHub release filenames at
# https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
# fp32 has no suffix, fp16/int8 do. Kept here so deps.install_models() and
# the backend agree on the canonical filenames.
MODEL_FILENAMES: dict[Quantization, str] = {
    "fp32": "kokoro-v1.0.onnx",
    "fp16": "kokoro-v1.0.fp16.onnx",
    "int8": "kokoro-v1.0.int8.onnx",
}
VOICES_FILENAME = "voices-v1.0.bin"


@register_tts_backend("kokoro")
class KokoroBackend(TTSBackend):
    sample_rate: ClassVar[int] = 24000
    channels: ClassVar[int] = 1
    sample_format: ClassVar[Literal["float32", "int16"]] = "float32"

    def __init__(self, data_dir: Path, quantization: Quantization = "fp16") -> None:
        self._audio_dir = data_dir / "audio"
        self._quantization = quantization
        self._kokoro: Kokoro | None = None

    @property
    def model_path(self) -> Path:
        return self._audio_dir / MODEL_FILENAMES[self._quantization]

    @property
    def voices_path(self) -> Path:
        return self._audio_dir / VOICES_FILENAME

    def models_present(self) -> bool:
        return self.model_path.exists() and self.voices_path.exists()

    def list_voices(self) -> list[VoiceInfo]:
        if self._kokoro is None:
            # Cold call — read the voices file directly so the options dropdown
            # works without paying the onnxruntime session cost.
            if not self.voices_path.exists():
                return []
            try:
                import numpy as np
                voices = np.load(self.voices_path)
                names = sorted(voices.keys())
            except Exception as e:
                log.warning("kokoro: failed to read voices file: %s", e)
                return []
        else:
            names = self._kokoro.get_voices()
        return [
            VoiceInfo(id=f"kokoro:{n}", raw=n, backend="kokoro", label=n)
            for n in names
        ]

    async def warmup(self) -> None:
        if self._kokoro is not None:
            return
        if not self.models_present():
            raise FileNotFoundError(
                f"Kokoro model files missing under {self._audio_dir}. "
                f"Run /voice-io install to fetch them.",
            )
        # Heavy import deferred to first use.
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
        log.debug("kokoro: warmed up (%s)", self._quantization)

    async def synthesize(
        self, text: str, voice_id: str, *, speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if self._kokoro is None:
            await self.warmup()
        assert self._kokoro is not None
        # Accept the namespaced id ("kokoro:af_bella") and the raw form for
        # tests / direct callers. Anything else is a config typo we want loud.
        raw = voice_id.removeprefix("kokoro:")
        async for samples, _sr in self._kokoro.create_stream(
            text, voice=raw, speed=speed,
        ):
            # samples is float32 mono @ 24kHz; .tobytes() is the PCM bytes the
            # output sink expects (declared by sample_format).
            yield samples.tobytes()

    async def aclose(self) -> None:
        # The onnxruntime InferenceSession releases on GC; dropping the ref
        # is sufficient for the toggle-off "free RAM" case.
        self._kokoro = None

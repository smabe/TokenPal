"""Local faster-whisper ASR backend.

Filename ``asr_*`` so the registry's input-side gate skips us on
ambient-only boots. Faster-whisper auto-downloads weights on first use;
``download_root`` keeps them under ``<data_dir>/audio/whisper/`` so an
air-gapped first session works once /voice-io install pre-fetches.

CPU + int8 by default, CUDA + float16 when ``TOKENPAL_ASR_DEVICE=cuda``.
Probing torch for a real GPU check would re-introduce the heavy import
we shed on the VAD path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from tokenpal.audio.base import ASRBackend
from tokenpal.audio.registry import register_asr_backend
from tokenpal.audio.util import pcm_int16_to_float32

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


def _pick_device_and_compute() -> tuple[str, str]:
    import os
    device = os.environ.get("TOKENPAL_ASR_DEVICE", "cpu").lower()
    if device == "cuda":
        return "cuda", "float16"
    return "cpu", "int8"


@register_asr_backend("local")
class LocalWhisperBackend(ASRBackend):
    sample_rate: ClassVar[int] = 16000

    def __init__(
        self,
        data_dir: Path,
        model_size: str = "small.en",
    ) -> None:
        self._data_dir = data_dir
        self._model_size = model_size
        self._model: WhisperModel | None = None

    @property
    def cache_root(self) -> Path:
        # download_root for faster-whisper. Keeps weights alongside the
        # rest of the audio data instead of in the user's HF cache.
        return self._data_dir / "audio" / "whisper"

    async def warmup(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        device, compute_type = _pick_device_and_compute()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute_type,
            download_root=str(self.cache_root),
        )
        log.debug(
            "faster-whisper: warmed up %s on %s/%s",
            self._model_size, device, compute_type,
        )

    async def transcribe(self, audio: bytes, *, language: str = "en") -> str:
        if self._model is None:
            await self.warmup()
        assert self._model is not None
        # beam_size=1 keeps latency low; only one utterance per call.
        segments, _info = self._model.transcribe(
            pcm_int16_to_float32(audio),
            language=language,
            beam_size=1,
        )
        return "".join(seg.text for seg in segments).strip()

    async def aclose(self) -> None:
        self._model = None

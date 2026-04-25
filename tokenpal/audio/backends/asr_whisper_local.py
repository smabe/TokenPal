"""Local faster-whisper ASR backend.

Filename ``asr_*`` so the registry's input-side gate skips us on
ambient-only boots. Faster-whisper auto-downloads model weights on
first use — install_models() (stage 8) pre-fetches them under
``<data_dir>/audio/whisper/`` so an air-gapped first session works.

CPU vs CUDA is auto-picked: faster-whisper exposes the same
WhisperModel API on both. ``compute_type='int8'`` on CPU and 'float16'
on CUDA balances speed and memory; users on a low-RAM laptop or a
chunky GPU server can override via config.audio.asr_compute_type
(future hook — current schema doesn't expose it).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from tokenpal.audio.base import ASRBackend
from tokenpal.audio.registry import register_asr_backend

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


def _pick_device_and_compute() -> tuple[str, str]:
    """CPU + int8 by default; CUDA + float16 if a GPU is visible.

    Probing torch.cuda would re-introduce the heavy import we shed in the
    Silero VAD stage; ctranslate2 (faster-whisper's backend) reads
    ``CUDA_VISIBLE_DEVICES`` directly and exposes a get_cuda_device_count
    helper, but importing it is what we're trying to avoid. The cheapest
    signal that's actually correlated with 'CUDA works here' is the
    presence of NVIDIA_VISIBLE_DEVICES (set in containers) or the env
    indicator users already wire. Default to CPU; users on a GPU box
    flip via TOKENPAL_ASR_DEVICE.
    """
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
        # faster-whisper writes a HuggingFace-style tree under here. We
        # hand it ``download_root`` so models stay alongside the rest of
        # the audio data instead of in the user's HF cache.
        return self._data_dir / "audio" / "whisper"

    async def warmup(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        device, compute_type = _pick_device_and_compute()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        # download_root lets us drop ASR models alongside Kokoro / VAD.
        # First call may hit the network if pre-fetch never ran; that's
        # the user's choice — _check_audio surfaces the missing files.
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
        import numpy as np

        # int16 PCM → float32 in [-1, 1], faster-whisper's expected shape.
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        samples /= 32768.0
        # transcribe returns (segments_iterator, info). We join segment
        # text — there's only one utterance per call (the FSM clips at
        # SPEECH_ENDED), so beam_size=1 keeps latency low.
        segments, _info = self._model.transcribe(
            samples,
            language=language,
            beam_size=1,
        )
        return "".join(seg.text for seg in segments).strip()

    async def aclose(self) -> None:
        self._model = None

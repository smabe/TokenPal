"""Remote ASR backend — POST to OpenAI-compatible /v1/audio/transcriptions.

2s connect timeout so a dead remote can't lock the wake pipeline; on
failure raises ASRUnreachableError so the asr.py facade can fall back
to LocalWhisperBackend.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import ClassVar

from tokenpal.audio.base import ASRBackend
from tokenpal.audio.registry import register_asr_backend

log = logging.getLogger(__name__)


class ASRUnreachableError(Exception):
    """Remote endpoint unreachable / errored. Caller should fall back."""


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM int16 mono in a WAV header for HTTP upload.

    The OpenAI-compatible endpoint expects a recognizable audio file —
    a header-less PCM blob fails server-side decode. WAV is trivially
    cheap to add (44 bytes) and works with every whisper server
    implementation on the planet.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


@register_asr_backend("server")
class RemoteWhisperBackend(ASRBackend):
    sample_rate: ClassVar[int] = 16000

    def __init__(
        self,
        server_url: str,
        model: str = "small.en",
        connect_timeout_s: float = 2.0,
        read_timeout_s: float = 30.0,
    ) -> None:
        if not server_url:
            raise ValueError("RemoteWhisperBackend requires a non-empty server_url")
        # Trim trailing slash so we can append "/v1/audio/transcriptions"
        # uniformly whether the user wrote http://host:port or .../
        self._server_url = server_url.rstrip("/")
        self._model = model
        self._connect_timeout = connect_timeout_s
        self._read_timeout = read_timeout_s

    @property
    def endpoint(self) -> str:
        return f"{self._server_url}/v1/audio/transcriptions"

    async def transcribe(self, audio: bytes, *, language: str = "en") -> str:
        # Lazy: httpx is already a project dep (cli health check, llm
        # backends), but importing here keeps the cold-start cost off
        # ambient-only boots that never call this backend.
        import httpx

        wav_bytes = _pcm_to_wav_bytes(audio, self.sample_rate)
        try:
            timeout = httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._read_timeout,
                pool=self._connect_timeout,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self.endpoint,
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data={
                        "model": self._model,
                        "language": language,
                        "response_format": "json",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            # One-line log — caller will fall back, no need for a stack trace.
            log.info("asr remote unreachable (%s): %s", self._server_url, e)
            raise ASRUnreachableError(str(e)) from e

        text = payload.get("text", "")
        return text.strip() if isinstance(text, str) else ""

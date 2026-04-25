"""ASR backend facade.

Picks LocalWhisperBackend or RemoteWhisperBackend based on
``config.audio.asr_backend``. Server mode falls back to local on a
2s connect timeout so a flaky remote never hangs the FSM.

We don't use the discover_backends path here because ASR backends have
different __init__ signatures (local takes a model_size, remote needs a
URL). A registry-side factory would help, but two backends is too few
to justify the abstraction yet.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tokenpal.audio.backends.asr_whisper_local import LocalWhisperBackend
from tokenpal.audio.backends.asr_whisper_remote import (
    ASRUnreachableError,
    RemoteWhisperBackend,
)
from tokenpal.audio.base import ASRBackend
from tokenpal.config.schema import AudioConfig

log = logging.getLogger(__name__)


class ASRWithFallback(ASRBackend):
    """Try remote first, fall back to local on ASRUnreachableError.

    The fallback path warms up the local backend lazily — if remote works
    consistently we never pay the faster-whisper download / load cost.
    """

    def __init__(self, primary: RemoteWhisperBackend, fallback: LocalWhisperBackend) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_warned = False

    async def warmup(self) -> None:
        # Don't pre-warm local — that defeats the "remote-only when remote
        # works" optimization. Local warms on first ASRUnreachableError.
        pass

    async def transcribe(self, audio: bytes, *, language: str = "en") -> str:
        try:
            return await self._primary.transcribe(audio, language=language)
        except ASRUnreachableError:
            if not self._fallback_warned:
                log.info(
                    "asr: server unreachable, falling back to local whisper",
                )
                self._fallback_warned = True
            return await self._fallback.transcribe(audio, language=language)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


def make_asr(config: AudioConfig, data_dir: Path) -> ASRBackend:
    """Construct the configured ASR backend.

    server mode wraps the remote client with a local fallback so the
    pipeline never blocks on a dead endpoint.
    """
    if config.asr_backend == "server":
        if not config.asr_server_url:
            log.warning(
                "asr_backend='server' but asr_server_url is empty — using local",
            )
            return LocalWhisperBackend(data_dir, model_size=config.asr_model_size)
        return ASRWithFallback(
            primary=RemoteWhisperBackend(
                config.asr_server_url, model=config.asr_model_size,
            ),
            fallback=LocalWhisperBackend(
                data_dir, model_size=config.asr_model_size,
            ),
        )
    return LocalWhisperBackend(data_dir, model_size=config.asr_model_size)

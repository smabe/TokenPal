"""Audio backend abstractions.

Plugin contract for TTS (and later ASR / wake-word). Mirrors the registry
pattern proven at tokenpal/senses/registry.py and tokenpal/actions/registry.py
so a future trained-voice backend drops in alongside ``KokoroBackend`` without
touching the session state machine, routing, or UI toggles.

Heavy deps (kokoro_onnx, numpy, sounddevice) stay inside concrete backends and
are imported lazily inside methods, not at module top, so an ambient-only boot
leaves the input-side wheels untouched. ``tests/test_audio/test_modularity.py``
guards the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import ClassVar, Literal


@dataclass(frozen=True)
class VoiceInfo:
    # ``id`` is the namespaced public form: "<backend>:<voice>" (e.g.
    # "kokoro:af_bella"). ``raw`` is the value the underlying backend
    # expects when calling its synth API.
    id: str
    raw: str
    backend: str
    label: str = ""


class TTSBackend(ABC):
    # Backends that emit at a non-24kHz rate set this; the playback sink
    # honors whatever the backend declares. Kokoro is 24000 / float32 mono.
    sample_rate: ClassVar[int]
    channels: ClassVar[int] = 1
    sample_format: ClassVar[Literal["float32", "int16"]] = "float32"

    @abstractmethod
    def list_voices(self) -> list[VoiceInfo]: ...

    @abstractmethod
    async def synthesize(
        self, text: str, voice_id: str, *, speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """Yield PCM chunks in the backend's declared format. Streaming-first;
        a buffer-only backend yields a single chunk."""

    async def warmup(self) -> None:
        """Lazy-load model weights on first use, not at import."""

    async def aclose(self) -> None:
        """Release model RAM on toggle-off."""

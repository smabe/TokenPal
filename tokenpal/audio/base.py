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
    def synthesize(
        self, text: str, voice_id: str, *, speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """Yield PCM chunks in the backend's declared format. Streaming-first;
        a buffer-only backend yields a single chunk.

        Declared without ``async`` (mypy: see asynchronous-iterators docs);
        concrete impls are async generators using ``yield``.
        """

    async def warmup(self) -> None:
        """Lazy-load model weights on first use, not at import."""

    async def aclose(self) -> None:
        """Release model RAM on toggle-off."""


class ASRBackend(ABC):
    # Whisper variants all want 16kHz mono — backends with different
    # requirements override. transcribe() is async because remote ASR
    # over HTTP needs to await the request without blocking the FSM.
    sample_rate: ClassVar[int] = 16000

    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str = "en") -> str:
        """Take int16 PCM mono samples (sample_rate Hz) and return the
        transcript text. Empty string is a valid result and the FSM treats
        it as 'no speech' — the wakeword fired on noise."""

    async def warmup(self) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class WakeEvent:
    # Which wakeword fired (e.g. "hey_jarvis", "hey_tokenpal") and its
    # confidence score. The session FSM uses this to switch from idle to
    # listening; downstream code generally only needs the score >= threshold
    # decision the backend has already made.
    model_name: str
    score: float


class WakeWordBackend(ABC):
    # 16kHz int16 mono — the de-facto wake-word audio format. openWakeWord
    # requires multiples of 80ms; 1280 samples = 80ms @ 16kHz is the
    # minimum-latency choice. Backends with different needs (Porcupine etc.)
    # override these.
    sample_rate: ClassVar[int] = 16000
    chunk_samples: ClassVar[int] = 1280

    @abstractmethod
    def detect(self, frame: bytes) -> WakeEvent | None:
        """Run one wake-word inference pass on a single PCM frame.

        ``frame`` is ``chunk_samples`` int16 mono samples (so
        ``len(frame) == chunk_samples * 2``). Returns a WakeEvent when the
        score crosses the configured threshold, else None.
        """

    async def warmup(self) -> None: ...

    async def aclose(self) -> None: ...

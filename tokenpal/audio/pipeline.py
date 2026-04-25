"""Audio pipeline boot.

Two independent toggles in ``[audio]``:

* ``speak_ambient_enabled`` — output only (TTS); never opens a mic.
* ``voice_conversation_enabled`` — output + input (TTS + wake + VAD + ASR).

The input branch lazy-imports ``openwakeword`` so an ambient-only boot leaves
the wake-word wheel untouched. ``tests/test_audio/test_modularity.py`` holds
the contract.

The pipeline owns the lifetime of the active TTS backend. ``tts()`` constructs
it on first call (or when toggling on after a previous ``aclose()``) so the
options-dropdown / list_voices path can read voice names from disk without
paying the onnxruntime session cost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from tokenpal.audio import registry
from tokenpal.config.schema import AudioConfig

if TYPE_CHECKING:
    from tokenpal.audio.base import TTSBackend

log = logging.getLogger(__name__)


@dataclass
class AudioPipeline:
    config: AudioConfig
    data_dir: Path
    _tts: TTSBackend | None = field(default=None, repr=False)

    def tts(self) -> TTSBackend:
        if self._tts is not None:
            return self._tts
        cls = registry.get_tts_backend(self.config.tts_backend)
        # KokoroBackend takes (data_dir, quantization). Future backends with
        # different signatures will need a registry-side factory hook; today
        # the only registered backend is kokoro, so the direct call is fine.
        self._tts = cls(self.data_dir, self.config.kokoro_quantization)  # type: ignore[call-arg]
        return self._tts

    async def aclose(self) -> None:
        if self._tts is not None:
            await self._tts.aclose()
            self._tts = None


def boot(config: AudioConfig, data_dir: Path) -> AudioPipeline:
    # Walk the backends package so @register_tts_backend has fired for
    # everything output-side. include_input is False — input-side modules
    # (asr_/wake_) aren't loaded for an ambient-only boot.
    registry.discover_backends(include_input=False)
    if config.voice_conversation_enabled:
        # Input-side gate: the wake-word wheel is the marker dep that's
        # forbidden in an ambient-only boot. Import here is what trips the
        # modularity anti-test.
        import openwakeword  # noqa: F401
    return AudioPipeline(config=config, data_dir=data_dir)

"""Audio pipeline boot.

Two independent toggles in ``[audio]``:

* ``speak_ambient_enabled`` — output only (TTS); never opens a mic.
* ``voice_conversation_enabled`` — output + input (TTS + wake + VAD + ASR).

The input branch lazy-imports ``openwakeword`` so an ambient-only boot leaves
the wake-word wheel untouched. ``tests/test_audio/test_modularity.py`` holds
the contract.

The pipeline owns the lifetime of the active TTS backend (``tts()``) and,
when voice mode is on, the InputPipeline (``start_input()`` /
``stop_input()``). The orchestrator wires its submit_user_input to
``on_voice_text`` so transcribed utterances flow into the brain queue with
``source='voice'``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from tokenpal.audio import registry
from tokenpal.config.schema import AudioConfig

if TYPE_CHECKING:
    from tokenpal.audio.base import TTSBackend
    from tokenpal.audio.input import InputPipeline

log = logging.getLogger(__name__)


@dataclass
class AudioPipeline:
    config: AudioConfig
    data_dir: Path
    _tts: TTSBackend | None = field(default=None, repr=False)
    _input: InputPipeline | None = field(default=None, repr=False)

    def tts(self) -> TTSBackend:
        if self._tts is not None:
            return self._tts
        cls = registry.get_tts_backend(self.config.tts_backend)
        # KokoroBackend takes (data_dir, quantization). Future backends with
        # different signatures will need a registry-side factory hook; today
        # the only registered backend is kokoro, so the direct call is fine.
        self._tts = cls(self.data_dir, self.config.kokoro_quantization)  # type: ignore[call-arg]
        return self._tts

    @property
    def input(self) -> InputPipeline | None:
        return self._input

    async def start_input(
        self,
        loop: asyncio.AbstractEventLoop,
        on_voice_text: Callable[[str], None],
    ) -> None:
        """Build + start the InputPipeline. No-op when voice mode is off
        or input is already running."""
        if not self.config.voice_conversation_enabled:
            return
        if self._input is not None:
            return
        # Lazy import — keeps ambient-only boots from pulling the input
        # module (which transitively imports openwakeword + faster-whisper
        # paths even though their heavy imports are method-local).
        from tokenpal.audio.input import InputPipeline

        self._input = InputPipeline(
            config=self.config,
            data_dir=self.data_dir,
            loop=loop,
            on_voice_text=on_voice_text,
        )
        try:
            await self._input.start()
        except FileNotFoundError as e:
            log.warning("voice input not started: %s", e)
            self._input = None
        except Exception:
            # PortAudio/RDP-redirected mics, missing devices, sounddevice
            # init failures — voice is optional, the brain must keep going.
            log.exception("voice input failed to start; continuing without voice")
            self._input = None

    async def stop_input(self) -> None:
        if self._input is not None:
            await self._input.stop()
            self._input = None

    async def aclose(self) -> None:
        if self._tts is not None:
            await self._tts.aclose()
            self._tts = None
        await self.stop_input()


def boot(config: AudioConfig, data_dir: Path) -> AudioPipeline:
    registry.discover_backends(include_input=False)
    if config.voice_conversation_enabled:
        # Marker import: forbidden under ambient-only by the modularity
        # anti-test. start_input() does the real input-side lazy-load.
        import openwakeword  # noqa: F401
    return AudioPipeline(config=config, data_dir=data_dir)

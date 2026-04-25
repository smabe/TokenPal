"""Audio pipeline boot.

Two independent toggles in ``[audio]``:

* ``speak_ambient_enabled`` — output only (TTS); never opens a mic.
* ``voice_conversation_enabled`` — output + input (TTS + wake + VAD + ASR).

The input branch lazy-imports ``sounddevice`` so an ambient-only boot leaves
the PortAudio wheel untouched. ``tests/test_audio/test_modularity.py``
holds this contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from tokenpal.config.schema import AudioConfig


@dataclass
class AudioPipeline:
    config: AudioConfig


def boot(config: AudioConfig) -> AudioPipeline:
    if config.voice_conversation_enabled:
        # Mic capture lives on PortAudio; importing here is the gate the
        # modularity test enforces. Real wake/VAD/ASR wiring lands in phase 3.
        import sounddevice  # noqa: F401
    return AudioPipeline(config=config)

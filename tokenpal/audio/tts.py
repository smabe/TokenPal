"""TTS speak facade — output-only routing + sentence-streaming playback.

Routing rules:
* ``typed`` requires ``speak_typed_replies_enabled`` (off by default).
* ``ambient`` requires ``speak_ambient_enabled``.
* ``voice`` requires ``voice_conversation_enabled``.

Playback:
* The text is split on ``.!?\\n`` and synthesized one sentence at a time so
  the first sentence starts playing before the rest is generated. Backends
  that already stream (Kokoro) yield mid-sentence chunks too; we just iterate.
* ``sounddevice.OutputStream`` is opened with the active backend's declared
  ``sample_rate`` / ``channels`` / ``sample_format`` so a future Piper backend
  at 22050Hz int16 plugs in without changes here.
* Drain-on-cancel: ``CancelledError`` aborts the stream so a typed turn
  arriving mid-narration silences instantly instead of finishing the queue.

If the audio deps or model files are missing we log and return. /voice-io
install is the path that makes audio actually fire — the brain shouldn't
crash because a user toggled ambient on but never ran install.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from tokenpal.audio import deps
from tokenpal.audio.types import InputSource

if TYPE_CHECKING:
    from tokenpal.audio.pipeline import AudioPipeline

log = logging.getLogger(__name__)

# Trailing capture so the punctuation rides with its sentence — re.split's
# capturing group keeps the delimiter in the output list.
_SENTENCE_SPLIT = re.compile(r"([.!?\n]+)")

# Two simultaneous OutputStreams on the same device produces unpredictable
# audio on macOS PortAudio. Serialize speak() calls — typed fire-and-forget
# + voice await can race when both toggles are on; lock makes them queue.
# Lazy-init so module import doesn't require a running event loop.
_PLAYBACK_LOCK: asyncio.Lock | None = None


def _playback_lock() -> asyncio.Lock:
    global _PLAYBACK_LOCK
    if _PLAYBACK_LOCK is None:
        _PLAYBACK_LOCK = asyncio.Lock()
    return _PLAYBACK_LOCK


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    out: list[str] = []
    buf = ""
    for piece in parts:
        if not piece:
            continue
        buf += piece
        if _SENTENCE_SPLIT.fullmatch(piece):
            s = buf.strip()
            if s:
                out.append(s)
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)
    return out


async def speak(
    text: str,
    *,
    source: InputSource,
    pipeline: AudioPipeline,
) -> None:
    """Speak ``text`` if the routing rules permit it."""
    cfg = pipeline.config
    if source == "typed" and not cfg.speak_typed_replies_enabled:
        return
    if source == "ambient" and not cfg.speak_ambient_enabled:
        return
    if source == "voice" and not cfg.voice_conversation_enabled:
        return

    if deps.missing_deps(include_input=False):
        log.debug("tts.speak: audio deps missing, skipping playback")
        return

    backend = pipeline.tts()
    try:
        await backend.warmup()
    except FileNotFoundError as e:
        log.debug("tts.speak: %s", e)
        return

    # Imports deferred to first real playback — keeps the modularity test
    # honest for ambient toggles that never actually fire (no models yet).
    import numpy as np
    import sounddevice as sd

    voice_id = cfg.tts_voice or "kokoro:af_bella"
    dtype = "float32" if backend.sample_format == "float32" else "int16"
    np_dtype = np.float32 if backend.sample_format == "float32" else np.int16

    async with _playback_lock():
        stream = sd.OutputStream(
            samplerate=backend.sample_rate,
            channels=backend.channels,
            dtype=dtype,
        )
        stream.start()
        loop = asyncio.get_running_loop()
        try:
            for sentence in _sentences(text):
                async for chunk in backend.synthesize(sentence, voice_id):
                    if not chunk:
                        continue
                    samples = np.frombuffer(chunk, dtype=np_dtype)
                    # OutputStream.write blocks until the device drains;
                    # the executor keeps the asyncio loop responsive so a
                    # typed-input cancellation can land between chunks.
                    await loop.run_in_executor(None, stream.write, samples)
        except asyncio.CancelledError:
            stream.abort(ignore_errors=True)
            raise
        finally:
            stream.stop(ignore_errors=True)
            stream.close(ignore_errors=True)

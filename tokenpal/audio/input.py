"""Voice input pipeline — daemon thread + wake → VAD → ASR → brain.

Capture loop runs in a daemon thread; the asyncio loop runs on whatever
thread Brain runs on. Cross-thread coordination uses
``loop.call_soon_threadsafe`` for the transcript hand-off and
``run_coroutine_threadsafe(...).result()`` to await ASR from the audio
thread (safe from a non-loop thread).

Lifecycle: ``start()`` warms + opens the mic; ``stop()`` sets cancel,
joins thread within 250ms, then closes the stream. The join-then-close
ordering avoids the macOS orange-dot-stuck bug.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tokenpal.audio.asr import make_asr
from tokenpal.audio.backends.wake_openwakeword import OpenWakeWordBackend
from tokenpal.audio.session import Action, Decision, VoiceSession, VoiceState
from tokenpal.audio.vad import SileroVAD, VadEvent
from tokenpal.config.schema import AudioConfig

if TYPE_CHECKING:
    from tokenpal.audio.base import ASRBackend, WakeWordBackend

log = logging.getLogger(__name__)

_FRAME_SAMPLES = 1280   # 80ms @ 16kHz, openwakeword's recommended granularity
_SAMPLE_RATE = 16000

_LISTENING_TIMEOUT_S = 5.0
_TICK_INTERVAL_S = 0.1

# Cap the in-flight utterance buffer at 30s of audio. Without this, a
# stuck-on-speech VAD or a sustained high-confidence false positive
# would grow the buffer until the heap dies. 30s @ 16kHz int16 = ~960KB.
_MAX_UTTERANCE_BYTES = 30 * _SAMPLE_RATE * 2


class InputPipeline:
    """Mic capture + wake/VAD/ASR loop running in a daemon thread."""

    def __init__(
        self,
        config: AudioConfig,
        data_dir: Path,
        loop: asyncio.AbstractEventLoop,
        on_voice_text: Callable[[str], None],
    ) -> None:
        self._config = config
        self._data_dir = data_dir
        self._loop = loop
        self._on_voice_text = on_voice_text

        self._wake: WakeWordBackend = OpenWakeWordBackend(
            data_dir,
            model_name="hey_jarvis",
            threshold=config.wakeword_threshold,
        )
        self._vad = SileroVAD(data_dir)
        self._asr: ASRBackend = make_asr(config, data_dir)
        self._fsm = VoiceSession(trailing_window_s=config.trailing_window_s)

        self._cancel = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: Any = None
        self._utterance_buffer = bytearray()
        self._listening_started_at: float | None = None
        self._atexit_registered = False

    async def start(self) -> None:
        if self._thread is not None:
            return
        # Warmup is independent across backends.
        await asyncio.gather(self._wake.warmup(), self._vad.warmup())

        # sounddevice import is delayed so a voice-mode-OFF boot leaves
        # PortAudio untouched.
        import sounddevice as sd
        stream = sd.RawInputStream(
            samplerate=_SAMPLE_RATE,
            blocksize=_FRAME_SAMPLES,
            channels=1,
            dtype="int16",
        )
        stream.start()
        self._stream = stream

        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, name="tokenpal-voice-input", daemon=True,
        )
        self._thread.start()

        if not self._atexit_registered:
            atexit.register(self._atexit_cleanup)
            self._atexit_registered = True
        log.info("voice input: started")

    async def stop(self) -> None:
        self._atexit_cleanup()

    def notify_tts_done(self) -> None:
        self._handle(self._fsm.on_tts_done(now=time.monotonic()))

    def notify_sensitive_app(self) -> None:
        self._paused.set()
        self._handle(self._fsm.on_sensitive_app())

    def notify_sensitive_app_cleared(self) -> None:
        self._paused.clear()

    def notify_typed_input(self) -> None:
        self._handle(self._fsm.on_typed_input())

    def _run(self) -> None:
        log.debug("voice input thread up")
        try:
            self._loop_forever()
        except Exception:
            log.exception("voice input thread crashed")
        log.debug("voice input thread exiting")

    def _loop_forever(self) -> None:
        last_tick = time.monotonic()
        while not self._cancel.is_set():
            assert self._stream is not None
            data, overflowed = self._stream.read(_FRAME_SAMPLES)
            if overflowed:
                log.debug("voice input: ring buffer overflow")
            frame = bytes(data)

            now = time.monotonic()
            if now - last_tick >= _TICK_INTERVAL_S:
                self._handle(self._fsm.tick(now=now))
                last_tick = now

            if self._paused.is_set():
                continue

            self._handle_frame(frame, now=now)

    def _handle_frame(self, frame: bytes, *, now: float) -> None:
        state = self._fsm.state
        if state == VoiceState.IDLE:
            event = self._wake.detect(frame)
            if event is not None:
                log.info(
                    "voice: wake (%s @ %.2f)", event.model_name, event.score,
                )
                self._utterance_buffer.clear()
                self._listening_started_at = now
                self._vad.reset()
                self._handle(self._fsm.on_wake())
            return

        if state == VoiceState.LISTENING:
            self._utterance_buffer.extend(frame)
            if len(self._utterance_buffer) > _MAX_UTTERANCE_BYTES:
                # VAD never fired SPEECH_ENDED on a 30s utterance — assume
                # stuck and force-close rather than balloon memory.
                log.warning("voice: utterance buffer cap hit, force-closing")
                self._handle(self._fsm.on_listening_timeout())
                return
            vad_event = self._vad.process(frame)
            if vad_event == VadEvent.SPEECH_ENDED:
                self._fsm.on_speech_ended()
                self._run_asr_blocking()
                return
            if (
                self._listening_started_at is not None
                and now - self._listening_started_at > _LISTENING_TIMEOUT_S
            ):
                log.info("voice: listening timeout, no speech")
                self._handle(self._fsm.on_listening_timeout())
            return

        if state == VoiceState.TRAILING:
            vad_event = self._vad.process(frame)
            if vad_event == VadEvent.SPEECH_STARTED:
                self._utterance_buffer.clear()
                self._listening_started_at = now
                self._handle(self._fsm.on_speech_started())
            return

        # SPEAKING: skip wake/VAD entirely. The brain's TTS loop owns the
        # output device while the buddy talks; mic frames here would feed
        # our own voice into wake detection.

    def _run_asr_blocking(self) -> None:
        audio = bytes(self._utterance_buffer)
        self._utterance_buffer.clear()
        if not audio:
            self._handle(self._fsm.on_transcript(""))
            return
        # run_coroutine_threadsafe(...).result() is safe here because we're
        # in a daemon thread, not the loop thread itself.
        future = asyncio.run_coroutine_threadsafe(
            self._asr.transcribe(audio), self._loop,
        )
        try:
            text = future.result(timeout=30.0)
        except Exception as e:
            log.warning("voice ASR failed: %s", e)
            text = ""
        self._handle(self._fsm.on_transcript(text))

    def _handle(self, decision: Decision) -> None:
        if decision.action == Action.SUBMIT:
            if decision.text:
                self._loop.call_soon_threadsafe(
                    self._on_voice_text, decision.text,
                )
        elif decision.action == Action.CLOSE_SESSION:
            self._utterance_buffer.clear()

    def _atexit_cleanup(self) -> None:
        # Order: cancel → join (250ms) → close stream. Closing before the
        # thread observes cancel leaves read() blocked on dead PortAudio
        # state and causes the macOS orange-dot-stuck bug.
        if self._cancel.is_set():
            return
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=0.25)
            self._thread = None
        if self._stream is not None:
            self._stream.stop(ignore_errors=True)
            self._stream.close(ignore_errors=True)
            self._stream = None
        log.info("voice input: stopped")

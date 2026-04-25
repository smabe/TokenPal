"""Voice input pipeline — daemon thread + wake → VAD → ASR → brain.

The capture loop runs in a daemon thread (matches the
``tokenpal/senses/_keyboard_bus.py`` pattern). The asyncio loop runs on
whatever thread Brain runs on. We bridge across with two primitives:

* ``loop.call_soon_threadsafe(queue.put_nowait, ...)`` — hot-path
  events. The brain's ``submit_user_input`` already wraps this.
* ``asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=...)``
  — for awaiting ASR from the audio thread. Per the plan failure-modes
  list this DOES NOT deadlock when called from a non-loop thread, only
  from the loop thread itself.

Lifecycle::

    start()  -> opens RawInputStream, spawns thread, registers atexit
    stop()   -> sets cancel event, joins thread (250ms timeout), closes
                stream. The 250ms join-then-close ordering is the macOS
                orange-dot-stuck workaround from the plan.

The pipeline is owned by AudioPipeline (stage 7b). External callers
notify it of TTS completion / sensitive-app changes via the
``notify_*`` methods so the FSM can drive the trailing-window logic.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tokenpal.audio.asr import make_asr
from tokenpal.audio.backends.wake_openwakeword import OpenWakeWordBackend
from tokenpal.audio.session import Action, VoiceSession, VoiceState
from tokenpal.audio.vad import SileroVAD, VadEvent
from tokenpal.config.schema import AudioConfig

if TYPE_CHECKING:
    from tokenpal.audio.base import ASRBackend, WakeWordBackend

log = logging.getLogger(__name__)

# Frame size for the mic stream. openwakeword recommends a multiple of
# 80ms; 1280 samples = 80ms @ 16kHz is the minimum-latency choice and
# divides cleanly into Silero VAD's 512-sample chunks (we feed the same
# frame to both — VAD chunks internally).
_FRAME_SAMPLES = 1280
_SAMPLE_RATE = 16000

# How long to buffer audio after wake fires before forcing close-on-no-
# speech. Without this, a wake on noise with no follow-up speech leaves
# the FSM in LISTENING forever.
_LISTENING_TIMEOUT_S = 5.0

# How often the FSM ticks for trailing-window hard close. 100ms is fine-
# grained enough that an 8s window closes within ~1% of target.
_TICK_INTERVAL_S = 0.1


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
        self._on_voice_text = on_voice_text  # called via call_soon_threadsafe

        self._wake: WakeWordBackend = OpenWakeWordBackend(
            data_dir,
            model_name="hey_jarvis",  # placeholder until hey_tokenpal trained
            threshold=config.wakeword_threshold,
        )
        self._vad = SileroVAD(data_dir)
        self._asr: ASRBackend = make_asr(config, data_dir)
        self._fsm = VoiceSession(trailing_window_s=config.trailing_window_s)

        self._cancel = threading.Event()
        self._paused = threading.Event()  # set during sensitive-app
        self._thread: threading.Thread | None = None
        # sounddevice.RawInputStream — typed as Any so mypy doesn't gripe
        # about the lazy import. Concrete type is sd.RawInputStream once
        # start() runs.
        self._stream: Any = None
        self._utterance_buffer = bytearray()
        self._listening_started_at: float | None = None
        self._atexit_registered = False

    # -------- public surface (asyncio side) ----------------------------

    async def start(self) -> None:
        if self._thread is not None:
            return
        await self._wake.warmup()
        await self._vad.warmup()
        # ASR warms lazily on first transcript so a healthy server-mode
        # path never pays the local-whisper load cost.

        # sounddevice import is delayed until start() so a voice-mode-OFF
        # boot leaves PortAudio untouched. The modularity test pins this
        # at the boot() level — input streams open here.
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
        """Brain calls this after a voice reply finishes playing."""
        self._dispatch(self._fsm.on_tts_done(now=self._now()))

    def notify_sensitive_app(self) -> None:
        """Brain detected a sensitive app — kill any in-flight voice work."""
        self._paused.set()
        self._dispatch(self._fsm.on_sensitive_app())

    def notify_sensitive_app_cleared(self) -> None:
        self._paused.clear()

    def notify_typed_input(self) -> None:
        """User typed mid-voice-session. Drop the voice path."""
        self._dispatch(self._fsm.on_typed_input())

    # -------- internals (audio thread side) ----------------------------

    def _now(self) -> float:
        # Single source for the FSM's clock so tests can swap it later.
        import time
        return time.monotonic()

    def _run(self) -> None:
        log.debug("voice input thread up")
        try:
            self._loop_forever()
        except Exception:
            log.exception("voice input thread crashed")
        log.debug("voice input thread exiting")

    def _loop_forever(self) -> None:
        last_tick = self._now()
        while not self._cancel.is_set():
            assert self._stream is not None
            data, overflowed = self._stream.read(_FRAME_SAMPLES)
            if overflowed:
                # ALSA / coreaudio overflowed the input buffer — usually
                # means our loop fell behind. Worth surfacing in logs but
                # not fatal; the wakeword is robust to dropped frames.
                log.debug("voice input: ring buffer overflow")
            frame = bytes(data)

            now = self._now()
            if now - last_tick >= _TICK_INTERVAL_S:
                self._dispatch(self._fsm.tick(now=now))
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
                self._dispatch(self._fsm.on_wake())
            return

        if state == VoiceState.LISTENING:
            self._utterance_buffer.extend(frame)
            vad_event = self._vad.process(frame)
            if vad_event == VadEvent.SPEECH_ENDED:
                self._dispatch(self._fsm.on_speech_ended())
                self._run_asr_blocking()
                return
            # Listening timeout — wake fired but the user never spoke.
            if (
                self._listening_started_at is not None
                and now - self._listening_started_at > _LISTENING_TIMEOUT_S
            ):
                log.info("voice: listening timeout, no speech")
                self._dispatch(self._fsm.on_listening_timeout())
            return

        if state == VoiceState.TRAILING:
            vad_event = self._vad.process(frame)
            if vad_event == VadEvent.SPEECH_STARTED:
                self._utterance_buffer.clear()
                self._listening_started_at = now
                self._dispatch(self._fsm.on_speech_started())
            return

        # SPEAKING: skip wake/VAD entirely. The brain's TTS loop owns
        # the audio device while the buddy talks; mic frames here would
        # just feed our own voice back into wake detection.

    def _run_asr_blocking(self) -> None:
        """Take the buffered utterance, run ASR, dispatch the result.

        Called from the audio thread. ``run_coroutine_threadsafe(...)
        .result()`` is the right primitive here per the plan: it only
        deadlocks when called from the loop thread, not a daemon thread.
        Wrapping a 30s ASR timeout caps the worst case (model fell over
        / took too long) and surfaces it as an empty transcript so the
        FSM closes cleanly.
        """
        audio = bytes(self._utterance_buffer)
        self._utterance_buffer.clear()
        if not audio:
            self._dispatch(self._fsm.on_transcript(""))
            return
        future = asyncio.run_coroutine_threadsafe(
            self._asr.transcribe(audio), self._loop,
        )
        try:
            text = future.result(timeout=30.0)
        except Exception as e:
            log.warning("voice ASR failed: %s", e)
            text = ""
        self._dispatch(self._fsm.on_transcript(text))

    # -------- action dispatcher ---------------------------------------

    def _dispatch(self, action: Action) -> None:
        if action == Action.NONE:
            return
        if action == Action.SUBMIT_TO_BRAIN:
            text = self._fsm.submit_text
            if text:
                self._loop.call_soon_threadsafe(self._on_voice_text, text)
        elif action == Action.CLOSE_SESSION:
            self._utterance_buffer.clear()
        # OPEN_MIC / CLOSE_MIC / RUN_ASR are signals to other layers — the
        # mic is always open in this implementation; ASR runs inline in
        # the thread above; CLOSE_MIC is a state-only event handled by
        # the FSM state itself.

    def _atexit_cleanup(self) -> None:
        """Set cancel, join with 250ms timeout, then close streams.

        Order matters: closing the stream before the thread observes the
        cancel event leaves the read() blocked on dead PortAudio state.
        On macOS this is what leaves the orange dot stuck until reboot.
        Per the plan failure-modes list.
        """
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

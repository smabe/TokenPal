"""Voice-conversation FSM.

Pure state machine: no I/O, no threads, no timers. Driven by the input
pipeline calling the ``on_*`` methods; each returns a ``Decision`` the
caller dispatches.

State diagram::

    IDLE  --wake-->  LISTENING
    LISTENING  --speech_ended-->  (run ASR)
                    --transcript "" / <2 chars-->  IDLE
                    --transcript text-->  SPEAKING (submit text)
    LISTENING  --listening_timeout-->  IDLE
    SPEAKING  --tts_done-->  TRAILING (deadline = now + window)
    TRAILING  --speech_started-->  LISTENING
    TRAILING  --tick(now > deadline)-->  IDLE  (hard close)
    *  --sensitive_app | typed_input-->  IDLE  (drain queues)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger(__name__)


class VoiceState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"
    TRAILING = "trailing"


class Action(StrEnum):
    NONE = "none"
    SUBMIT = "submit"          # caller hands transcript to the brain
    CLOSE_SESSION = "close"    # caller drains queues + closes mic


@dataclass(frozen=True)
class Decision:
    action: Action = Action.NONE
    text: str = ""


_NONE = Decision()
_CLOSE = Decision(Action.CLOSE_SESSION)

# Below this length the FSM treats an ASR transcript as a false fire and
# closes silently. Char count avoids a tokenizer round-trip; "ok" / "go"
# still pass at len > 1, hum / cough fragments don't.
_MIN_TRANSCRIPT_LEN = 2


@dataclass
class VoiceSession:
    trailing_window_s: float = 8.0
    state: VoiceState = VoiceState.IDLE
    _trailing_deadline: float | None = field(default=None, repr=False)

    def _set(self, new_state: VoiceState) -> None:
        log.debug("voice-fsm: %s -> %s", self.state, new_state)
        self.state = new_state

    def on_wake(self) -> Decision:
        # Wake during LISTENING / SPEAKING / TRAILING is ignored so a phantom
        # retrigger doesn't interrupt an in-flight reply.
        if self.state == VoiceState.IDLE:
            self._set(VoiceState.LISTENING)
        return _NONE

    def on_speech_ended(self) -> Decision:
        # Caller runs ASR inline and feeds on_transcript; FSM only tracks state.
        return _NONE

    def on_transcript(self, text: str) -> Decision:
        if self.state != VoiceState.LISTENING:
            return _NONE
        cleaned = text.strip()
        if len(cleaned) < _MIN_TRANSCRIPT_LEN:
            self._trailing_deadline = None
            self._set(VoiceState.IDLE)
            return _NONE
        self._set(VoiceState.SPEAKING)
        return Decision(Action.SUBMIT, cleaned)

    def on_tts_done(self, *, now: float) -> Decision:
        if self.state != VoiceState.SPEAKING:
            return _NONE
        self._trailing_deadline = now + self.trailing_window_s
        self._set(VoiceState.TRAILING)
        return _NONE

    def on_speech_started(self) -> Decision:
        if self.state != VoiceState.TRAILING:
            return _NONE
        self._trailing_deadline = None
        self._set(VoiceState.LISTENING)
        return _NONE

    def tick(self, *, now: float) -> Decision:
        # Hard close at deadline regardless of VAD — TV / music in the room
        # would otherwise hold the window open forever.
        if (
            self.state == VoiceState.TRAILING
            and self._trailing_deadline is not None
            and now >= self._trailing_deadline
        ):
            self._trailing_deadline = None
            self._set(VoiceState.IDLE)
            return _CLOSE
        return _NONE

    def on_listening_timeout(self) -> Decision:
        if self.state != VoiceState.LISTENING:
            return _NONE
        self._set(VoiceState.IDLE)
        return _CLOSE

    def on_sensitive_app(self) -> Decision:
        if self.state == VoiceState.IDLE:
            return _NONE
        self._trailing_deadline = None
        self._set(VoiceState.IDLE)
        return _CLOSE

    def on_typed_input(self) -> Decision:
        if self.state == VoiceState.IDLE:
            return _NONE
        self._trailing_deadline = None
        self._set(VoiceState.IDLE)
        return _CLOSE

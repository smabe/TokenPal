"""Voice-conversation FSM.

Pure state machine — no I/O, no threads, no timers. The pipeline glue
in stage 7 owns the mic / wake / VAD / ASR / TTS loops and drives this
FSM by calling the ``on_*`` methods. Each call returns an
``Action`` the caller dispatches: open_mic, close_mic, run_asr,
submit_to_brain, close_session.

Why split it this way: the "trailing window must hard-close at 8s"
invariant + the "<2-token transcript closes silently" rule + the
"sensitive app cancels everything" rule all interact, and they're
easier to verify against a pure FSM than against a real pipeline with
hardware. The plan calls for a three-test trailing-window suite
upstream — those run in stage 7 against the full glue, exercising
this FSM through the same surface.

State diagram::

    IDLE  --wake-->  LISTENING
    LISTENING  --speech_ended-->  (run ASR)
                    --transcript "" / <2 tokens-->  IDLE
                    --transcript text-->  (submit) -> SPEAKING
    LISTENING  --listening_timeout-->  IDLE
    SPEAKING  --tts_done-->  TRAILING (deadline = now + window)
    TRAILING  --speech_started-->  LISTENING
    TRAILING  --tick(now > deadline)-->  IDLE
    *  --sensitive_app | typed_input-->  IDLE  (drains queues)
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
    OPEN_MIC = "open_mic"
    CLOSE_MIC = "close_mic"
    RUN_ASR = "run_asr"
    SUBMIT_TO_BRAIN = "submit_to_brain"  # paired with .submit_text below
    CLOSE_SESSION = "close_session"


# Below this length the FSM treats an ASR transcript as a false fire and
# closes silently. Plan calls for "<2 tokens" — using char count instead
# avoids a tokenizer round-trip; "ok" / "go" still pass at len > 1, but
# hum / cough fragments don't.
_MIN_TRANSCRIPT_LEN = 2


@dataclass
class VoiceSession:
    """Voice-conversation state machine.

    Hold onto an instance per pipeline run. ``state`` is the current FSM
    node; ``last_action`` is whatever the most recent transition decided
    to do (for tests / observability). ``submit_text`` carries the
    transcript when ``last_action == SUBMIT_TO_BRAIN``.
    """

    trailing_window_s: float = 8.0
    state: VoiceState = VoiceState.IDLE
    submit_text: str = ""
    last_action: Action = Action.NONE
    _trailing_deadline: float | None = field(default=None, repr=False)

    def _set(self, new_state: VoiceState, action: Action) -> Action:
        log.debug("voice-fsm: %s -> %s (%s)", self.state, new_state, action)
        self.state = new_state
        self.last_action = action
        return action

    def on_wake(self) -> Action:
        """Wake word fired. Idle → Listening (open mic if needed)."""
        if self.state == VoiceState.IDLE:
            return self._set(VoiceState.LISTENING, Action.OPEN_MIC)
        # Wake firing during LISTENING / SPEAKING / TRAILING is a no-op:
        # the mic is already in the right state and we don't want a
        # double-trigger to interrupt an in-flight reply.
        log.debug("voice-fsm: wake ignored in state=%s", self.state)
        self.last_action = Action.NONE
        return Action.NONE

    def on_speech_ended(self) -> Action:
        """VAD reports the user stopped talking. Run ASR on the buffer."""
        if self.state != VoiceState.LISTENING:
            self.last_action = Action.NONE
            return Action.NONE
        return self._set(VoiceState.LISTENING, Action.RUN_ASR)

    def on_transcript(self, text: str) -> Action:
        """ASR returned. Empty / too-short → close silently. Else submit."""
        if self.state != VoiceState.LISTENING:
            self.last_action = Action.NONE
            return Action.NONE
        cleaned = text.strip()
        if len(cleaned) < _MIN_TRANSCRIPT_LEN:
            # Wake fired on a noise; the user hasn't said anything
            # meaningful. Close out and let the next wake try again.
            self._trailing_deadline = None
            return self._set(VoiceState.IDLE, Action.CLOSE_MIC)
        self.submit_text = cleaned
        return self._set(VoiceState.SPEAKING, Action.SUBMIT_TO_BRAIN)

    def on_tts_done(self, *, now: float) -> Action:
        """TTS finished. Open the trailing window for follow-up without re-wake."""
        if self.state != VoiceState.SPEAKING:
            self.last_action = Action.NONE
            return Action.NONE
        self._trailing_deadline = now + self.trailing_window_s
        return self._set(VoiceState.TRAILING, Action.OPEN_MIC)

    def on_speech_started(self) -> Action:
        """Mid-trailing-window speech. Re-enter listening (no re-wake)."""
        if self.state != VoiceState.TRAILING:
            self.last_action = Action.NONE
            return Action.NONE
        self._trailing_deadline = None
        return self._set(VoiceState.LISTENING, Action.NONE)

    def tick(self, *, now: float) -> Action:
        """Periodic tick — fires the trailing-window hard close.

        Hard close at deadline regardless of VAD state — a TV / music in
        the room would otherwise hold the window open forever. Per plan.
        """
        if (
            self.state == VoiceState.TRAILING
            and self._trailing_deadline is not None
            and now >= self._trailing_deadline
        ):
            self._trailing_deadline = None
            return self._set(VoiceState.IDLE, Action.CLOSE_MIC)
        self.last_action = Action.NONE
        return Action.NONE

    def on_listening_timeout(self) -> Action:
        """Wake fired but no speech ever started — false alarm."""
        if self.state != VoiceState.LISTENING:
            self.last_action = Action.NONE
            return Action.NONE
        return self._set(VoiceState.IDLE, Action.CLOSE_MIC)

    def on_sensitive_app(self) -> Action:
        """Privacy: a sensitive app is foregrounded. Tear everything down."""
        if self.state == VoiceState.IDLE:
            self.last_action = Action.NONE
            return Action.NONE
        self._trailing_deadline = None
        return self._set(VoiceState.IDLE, Action.CLOSE_SESSION)

    def on_typed_input(self) -> Action:
        """User typed mid-session. Typed wins — kill the voice path."""
        if self.state == VoiceState.IDLE:
            self.last_action = Action.NONE
            return Action.NONE
        self._trailing_deadline = None
        return self._set(VoiceState.IDLE, Action.CLOSE_SESSION)

"""VoiceSession FSM tests.

These pin the routing rules from plans/say-what.md:

* wake from idle → LISTENING (open mic)
* speech ended → run ASR
* empty / too-short transcript → silent close
* good transcript → SUBMIT_TO_BRAIN, then SPEAKING
* tts_done → TRAILING with deadline
* speech started during trailing → back to LISTENING
* tick past deadline → hard-close (regardless of VAD)
* sensitive app or typed input → CLOSE_SESSION from any state
"""

from __future__ import annotations

from tokenpal.audio.session import (
    Action,
    VoiceSession,
    VoiceState,
)


def test_wake_from_idle_opens_mic() -> None:
    s = VoiceSession()
    assert s.on_wake() == Action.OPEN_MIC
    assert s.state == VoiceState.LISTENING


def test_double_wake_is_no_op() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_wake() == Action.NONE
    assert s.state == VoiceState.LISTENING


def test_speech_ended_triggers_asr() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_speech_ended() == Action.RUN_ASR


def test_empty_transcript_closes_silently() -> None:
    s = VoiceSession()
    s.on_wake()
    s.on_speech_ended()
    assert s.on_transcript("") == Action.CLOSE_MIC
    assert s.state == VoiceState.IDLE


def test_short_transcript_closes_silently() -> None:
    # "h" is below MIN_TRANSCRIPT_LEN — single-char hum/cough fragments.
    s = VoiceSession()
    s.on_wake()
    s.on_speech_ended()
    assert s.on_transcript("h") == Action.CLOSE_MIC
    assert s.state == VoiceState.IDLE


def test_good_transcript_submits_and_enters_speaking() -> None:
    s = VoiceSession()
    s.on_wake()
    s.on_speech_ended()
    action = s.on_transcript("hey what's up")
    assert action == Action.SUBMIT_TO_BRAIN
    assert s.submit_text == "hey what's up"
    assert s.state == VoiceState.SPEAKING


def test_tts_done_starts_trailing_window() -> None:
    s = VoiceSession(trailing_window_s=8.0)
    s.on_wake()
    s.on_speech_ended()
    s.on_transcript("hello")
    assert s.on_tts_done(now=100.0) == Action.OPEN_MIC
    assert s.state == VoiceState.TRAILING


def test_trailing_speech_started_returns_to_listening() -> None:
    s = VoiceSession()
    s.on_wake()
    s.on_speech_ended()
    s.on_transcript("hello")
    s.on_tts_done(now=100.0)
    s.on_speech_started()
    assert s.state == VoiceState.LISTENING


def test_trailing_window_hard_closes_at_deadline() -> None:
    """Plan invariant: hard close regardless of VAD state.

    A TV in the background would otherwise keep us in trailing
    forever. The tick must fire even if speech_started never came.
    """
    s = VoiceSession(trailing_window_s=8.0)
    s.on_wake()
    s.on_speech_ended()
    s.on_transcript("hello")
    s.on_tts_done(now=100.0)
    # 7.9s in: still trailing.
    assert s.tick(now=107.9) == Action.NONE
    assert s.state == VoiceState.TRAILING
    # 8.0s in: hard close.
    assert s.tick(now=108.0) == Action.CLOSE_MIC
    assert s.state == VoiceState.IDLE


def test_listening_timeout_returns_to_idle() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_listening_timeout() == Action.CLOSE_MIC
    assert s.state == VoiceState.IDLE


def test_sensitive_app_kills_session_from_any_state() -> None:
    for state in (
        VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.TRAILING,
    ):
        s = VoiceSession()
        s.state = state
        s._trailing_deadline = 999.0  # would otherwise keep ticking
        assert s.on_sensitive_app() == Action.CLOSE_SESSION
        assert s.state == VoiceState.IDLE
        assert s._trailing_deadline is None


def test_typed_input_kills_session_from_any_state() -> None:
    for state in (
        VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.TRAILING,
    ):
        s = VoiceSession()
        s.state = state
        assert s.on_typed_input() == Action.CLOSE_SESSION
        assert s.state == VoiceState.IDLE


def test_typed_input_in_idle_is_no_op() -> None:
    # User types when there's no voice session. CLOSE_SESSION on idle
    # would be a meaningless instruction to the pipeline.
    s = VoiceSession()
    assert s.on_typed_input() == Action.NONE
    assert s.state == VoiceState.IDLE


def test_tick_outside_trailing_is_no_op() -> None:
    s = VoiceSession()
    # IDLE — no deadline, tick should never fire CLOSE_MIC.
    assert s.tick(now=99999.0) == Action.NONE
    assert s.state == VoiceState.IDLE

"""VoiceSession FSM tests.

Pin the routing rules from plans/say-what.md:

* wake from idle → LISTENING
* good transcript → Decision(SUBMIT, text), state = SPEAKING
* empty / short transcript → silent close to IDLE
* tts_done → TRAILING with deadline
* speech started during trailing → back to LISTENING
* tick past deadline → CLOSE_SESSION (regardless of VAD)
* sensitive app or typed input → CLOSE_SESSION from any non-idle state
"""

from __future__ import annotations

from tokenpal.audio.session import (
    Action,
    Decision,
    VoiceSession,
    VoiceState,
)


def test_wake_from_idle_enters_listening() -> None:
    s = VoiceSession()
    assert s.on_wake() == Decision()
    assert s.state == VoiceState.LISTENING


def test_double_wake_is_no_op() -> None:
    s = VoiceSession()
    s.on_wake()
    s.on_wake()
    assert s.state == VoiceState.LISTENING


def test_speech_ended_stays_in_listening() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_speech_ended() == Decision()
    assert s.state == VoiceState.LISTENING


def test_empty_transcript_closes_silently() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_transcript("") == Decision()
    assert s.state == VoiceState.IDLE


def test_short_transcript_closes_silently() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_transcript("h") == Decision()
    assert s.state == VoiceState.IDLE


def test_good_transcript_submits_and_enters_speaking() -> None:
    s = VoiceSession()
    s.on_wake()
    decision = s.on_transcript("hey what's up")
    assert decision == Decision(Action.SUBMIT, "hey what's up")
    assert s.state == VoiceState.SPEAKING


def test_tts_done_starts_trailing_window() -> None:
    s = VoiceSession(trailing_window_s=8.0)
    s.on_wake()
    s.on_transcript("hello")
    s.on_tts_done(now=100.0)
    assert s.state == VoiceState.TRAILING


def test_trailing_speech_started_returns_to_listening() -> None:
    s = VoiceSession()
    s.on_wake()
    s.on_transcript("hello")
    s.on_tts_done(now=100.0)
    s.on_speech_started()
    assert s.state == VoiceState.LISTENING


def test_trailing_window_hard_closes_at_deadline() -> None:
    """Plan invariant: hard close regardless of VAD. A TV in the room would
    otherwise keep us in trailing forever."""
    s = VoiceSession(trailing_window_s=8.0)
    s.on_wake()
    s.on_transcript("hello")
    s.on_tts_done(now=100.0)
    assert s.tick(now=107.9) == Decision()
    assert s.state == VoiceState.TRAILING
    assert s.tick(now=108.0) == Decision(Action.CLOSE_SESSION)
    assert s.state == VoiceState.IDLE


def test_listening_timeout_returns_to_idle() -> None:
    s = VoiceSession()
    s.on_wake()
    assert s.on_listening_timeout() == Decision(Action.CLOSE_SESSION)
    assert s.state == VoiceState.IDLE


def test_sensitive_app_kills_session_from_any_state() -> None:
    for state in (
        VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.TRAILING,
    ):
        s = VoiceSession()
        s.state = state
        s._trailing_deadline = 999.0
        assert s.on_sensitive_app() == Decision(Action.CLOSE_SESSION)
        assert s.state == VoiceState.IDLE
        assert s._trailing_deadline is None


def test_typed_input_kills_session_from_any_state() -> None:
    for state in (
        VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.TRAILING,
    ):
        s = VoiceSession()
        s.state = state
        assert s.on_typed_input() == Decision(Action.CLOSE_SESSION)
        assert s.state == VoiceState.IDLE


def test_typed_input_in_idle_is_no_op() -> None:
    s = VoiceSession()
    assert s.on_typed_input() == Decision()
    assert s.state == VoiceState.IDLE


def test_tick_outside_trailing_is_no_op() -> None:
    s = VoiceSession()
    assert s.tick(now=99999.0) == Decision()
    assert s.state == VoiceState.IDLE

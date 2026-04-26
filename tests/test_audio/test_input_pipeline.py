"""InputPipeline lifecycle + dispatcher tests.

Real mic capture / wake / ASR can't run in CI. We mock the three
backends + sounddevice so the focus is on:

* dispatch routing — SUBMIT_TO_BRAIN fires on_voice_text via the loop
* notify_* methods drive the FSM correctly
* atexit cleanup is idempotent

Three-test trailing-window suite from done-criteria: that's a manual
smoke since it needs real audio. We have unit coverage for the FSM
side of those scenarios in test_session.py.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest import mock

from tokenpal.audio.input import InputPipeline
from tokenpal.audio.session import VoiceState
from tokenpal.config.schema import AudioConfig


def _make_pipeline(
    tmp_path: Path,
    on_voice_text=None,
    voice_enabled: bool = True,
) -> InputPipeline:
    cfg = AudioConfig(voice_conversation_enabled=voice_enabled)
    loop = asyncio.new_event_loop()
    return InputPipeline(
        config=cfg,
        data_dir=tmp_path,
        loop=loop,
        on_voice_text=on_voice_text or (lambda _t: None),
    )


def test_init_does_not_open_stream(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)
    assert p._stream is None
    assert p._thread is None
    p._loop.close()


def test_handle_submit_calls_callback(tmp_path: Path) -> None:
    received: list[str] = []

    def on_voice_text(text: str) -> None:
        received.append(text)

    p = _make_pipeline(tmp_path, on_voice_text=on_voice_text)
    p._fsm.on_wake()
    decision = p._fsm.on_transcript("hello there")
    p._handle(decision)
    p._loop.run_until_complete(asyncio.sleep(0))
    assert received == ["hello there"]
    p._loop.close()


def test_notify_typed_input_drains_to_idle(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)
    p._utterance_buffer.extend(b"\x00" * 100)
    p._fsm.state = VoiceState.LISTENING

    p.notify_typed_input()

    assert p._fsm.state == VoiceState.IDLE
    # CLOSE_SESSION clears the buffer so a stale fragment doesn't leak
    # into the next turn.
    assert len(p._utterance_buffer) == 0
    p._loop.close()


def test_notify_sensitive_app_pauses_and_kills_session(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)
    p._fsm.state = VoiceState.LISTENING

    p.notify_sensitive_app()

    assert p._fsm.state == VoiceState.IDLE
    assert p._paused.is_set()

    p.notify_sensitive_app_cleared()
    assert not p._paused.is_set()
    p._loop.close()


def test_notify_tts_done_transitions_speaking_to_trailing(
    tmp_path: Path,
) -> None:
    p = _make_pipeline(tmp_path)
    p._fsm.state = VoiceState.SPEAKING

    p.notify_tts_done()

    assert p._fsm.state == VoiceState.TRAILING
    p._loop.close()


def test_atexit_cleanup_is_idempotent(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)
    # No thread / stream allocated, but cleanup must be safe to call
    # twice — atexit can run alongside an explicit stop().
    p._atexit_cleanup()
    p._atexit_cleanup()
    assert p._thread is None
    assert p._stream is None
    p._loop.close()


async def test_start_warms_backends_and_spawns_thread(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)

    # Warmup goes through wake.warmup() and vad.warmup() — both raise
    # FileNotFoundError without weights. Patch them out, plus the
    # sounddevice stream so we don't actually open a mic.
    p._wake.warmup = mock.AsyncMock(return_value=None)
    p._vad.warmup = mock.AsyncMock(return_value=None)

    fake_stream = mock.MagicMock()
    fake_sd = mock.MagicMock()
    fake_sd.RawInputStream = mock.MagicMock(return_value=fake_stream)

    with mock.patch.dict(
        __import__("sys").modules, {"sounddevice": fake_sd},
    ):
        await p.start()

    assert p._thread is not None
    assert isinstance(p._thread, threading.Thread)
    assert p._thread.daemon is True
    fake_stream.start.assert_called_once()

    await p.stop()
    fake_stream.stop.assert_called_once()
    fake_stream.close.assert_called_once()
    p._loop.close()


async def test_start_is_idempotent(tmp_path: Path) -> None:
    p = _make_pipeline(tmp_path)
    p._wake.warmup = mock.AsyncMock(return_value=None)
    p._vad.warmup = mock.AsyncMock(return_value=None)

    fake_stream = mock.MagicMock()
    fake_sd = mock.MagicMock()
    fake_sd.RawInputStream = mock.MagicMock(return_value=fake_stream)

    with mock.patch.dict(
        __import__("sys").modules, {"sounddevice": fake_sd},
    ):
        await p.start()
        thread = p._thread
        await p.start()  # second call — should not spawn another thread
        assert p._thread is thread

    await p.stop()
    p._loop.close()

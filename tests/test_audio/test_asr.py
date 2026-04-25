"""ASR backends + facade tests.

Local Whisper isn't exercised against weights — like Kokoro, that's a
manual smoke. We pin: registration, server URL handling, WAV
serialization, the timeout/fallback decision tree, and the empty-text
behavior the FSM relies on to silently close on noise wakes.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest import mock

import httpx
import pytest

from tokenpal.audio import asr
from tokenpal.audio.backends.asr_whisper_local import LocalWhisperBackend
from tokenpal.audio.backends.asr_whisper_remote import (
    ASRUnreachableError,
    RemoteWhisperBackend,
    _pcm_to_wav_bytes,
)
from tokenpal.audio.registry import (
    discover_backends,
    get_asr_backend,
    registered_asr_backends,
)
from tokenpal.config.schema import AudioConfig


def test_backends_registered() -> None:
    discover_backends(include_input=True)
    assert {"local", "server"}.issubset(registered_asr_backends())
    assert get_asr_backend("local") is LocalWhisperBackend
    assert get_asr_backend("server") is RemoteWhisperBackend


def test_pcm_to_wav_bytes_round_trip() -> None:
    pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    wav_bytes = _pcm_to_wav_bytes(pcm, 16000)
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.readframes(4) == pcm


def test_remote_requires_url() -> None:
    with pytest.raises(ValueError):
        RemoteWhisperBackend("")


def test_remote_strips_trailing_slash() -> None:
    b = RemoteWhisperBackend("http://gpu:8080/")
    assert b.endpoint == "http://gpu:8080/v1/audio/transcriptions"


async def test_remote_returns_text_on_success() -> None:
    backend = RemoteWhisperBackend("http://gpu:8080", model="small.en")

    async def fake_post(self, url, **kwargs):
        # raise_for_status() needs the request attached on the response;
        # pass it through so the happy-path assertion isn't a stub artifact.
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"text": "hello world"}, request=req)

    with mock.patch.object(httpx.AsyncClient, "post", fake_post):
        text = await backend.transcribe(b"\x00" * 1024)
    assert text == "hello world"


async def test_remote_raises_on_request_error() -> None:
    backend = RemoteWhisperBackend("http://gpu:8080")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    with mock.patch.object(httpx.AsyncClient, "post", fake_post):
        with pytest.raises(ASRUnreachableError):
            await backend.transcribe(b"\x00" * 1024)


async def test_facade_falls_back_to_local_on_unreachable(
    tmp_path: Path,
) -> None:
    cfg = AudioConfig(asr_backend="server", asr_server_url="http://gpu:8080")
    backend = asr.make_asr(cfg, tmp_path)
    assert isinstance(backend, asr.ASRWithFallback)

    async def boom(audio, *, language="en"):
        raise ASRUnreachableError("dead")

    fallback_called = {"n": 0}

    async def fake_local(audio, *, language="en"):
        fallback_called["n"] += 1
        return "from local"

    with mock.patch.object(backend._primary, "transcribe", boom), \
         mock.patch.object(backend._fallback, "transcribe", fake_local):
        text = await backend.transcribe(b"\x00" * 1024)
    assert text == "from local"
    assert fallback_called["n"] == 1


def test_facade_uses_local_when_server_url_empty(tmp_path: Path) -> None:
    cfg = AudioConfig(asr_backend="server", asr_server_url="")
    backend = asr.make_asr(cfg, tmp_path)
    # Empty url collapses to local — better than crashing inside the FSM.
    assert isinstance(backend, LocalWhisperBackend)


def test_facade_local_for_local_backend(tmp_path: Path) -> None:
    cfg = AudioConfig(asr_backend="local")
    backend = asr.make_asr(cfg, tmp_path)
    assert isinstance(backend, LocalWhisperBackend)

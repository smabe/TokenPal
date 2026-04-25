"""Server ASR endpoint tests.

We don't load real Whisper weights — patch _ensure_model to return a
stub that mirrors faster-whisper's transcribe() shape (segments
generator + info object). Covers: WAV decode, PCM → float32 conversion,
text join, and the server-without-[audio] 503 path.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from unittest import mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tokenpal.server import routes_audio
from tokenpal.server.app import create_app


@dataclass
class _FakeSegment:
    text: str


class _FakeWhisper:
    def transcribe(self, samples, language="en", beam_size=1):
        # Mirror the (segments_iterator, info) return shape.
        segs: Iterator[_FakeSegment] = iter([
            _FakeSegment(" hello"), _FakeSegment(" world."),
        ])
        return segs, mock.MagicMock(language=language)


@pytest.fixture
def client():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    application = create_app(ollama_url="http://fake-ollama:11434")
    # Match the existing pattern in test_routes_models — provide a stub
    # ollama client so the lifespan startup doesn't try to phone home.
    application.state.ollama_client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_transport),
    )
    with TestClient(application) as c:
        yield c


def _wav_payload(samples_int16: bytes, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples_int16)
    return buf.getvalue()


def test_transcriptions_happy_path(client):
    async def fake_ensure(_size: str):
        return _FakeWhisper()

    with mock.patch.object(routes_audio, "_ensure_model", fake_ensure):
        wav = _wav_payload(b"\x10\x00" * 16000)  # 1s of quiet tone
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav, "audio/wav")},
            data={"model": "small.en", "language": "en"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # Segments get joined and stripped — the leading space on " hello"
    # would otherwise surface, would surprise an OpenAI-pattern caller.
    assert body["text"] == "hello world."
    assert body["language"] == "en"


def test_transcriptions_returns_503_when_model_missing(client):
    async def fake_ensure(_size: str):
        raise HTTPException(
            status_code=503,
            detail="ASR not available on this server — install with pip install tokenpal[audio].",
        )

    with mock.patch.object(routes_audio, "_ensure_model", fake_ensure):
        wav = _wav_payload(b"\x00\x00" * 100)
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav, "audio/wav")},
        )
    assert resp.status_code == 503
    assert "ASR not available" in resp.json()["detail"]


def test_transcriptions_rejects_non_wav(client):
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.bin", b"not-a-wav", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "WAV" in resp.json()["detail"]


def test_transcriptions_rejects_stereo(client):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)  # stereo — server requires mono
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00\x00\x00" * 100)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
    )
    assert resp.status_code == 400
    assert "mono" in resp.json()["detail"].lower()

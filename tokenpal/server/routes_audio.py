"""OpenAI-compatible /v1/audio/transcriptions endpoint.

Mounted at /v1 so the path matches OpenAI exactly — RemoteWhisperBackend
and any other whisper-server client drop in without URL juggling.
faster-whisper loads lazily; a server running without [audio] extras
returns a clean 503. Compute is configurable via env:

    TOKENPAL_ASR_DEVICE=cuda
    TOKENPAL_ASR_COMPUTE_TYPE=float16
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from tokenpal.audio.util import pcm_int16_to_float32

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

router = APIRouter()

# Process-global model singleton — first request pays the load cost,
# subsequent requests share it. Concurrency: faster-whisper is
# thread-safe per call but we serialize behind a lock anyway because
# loading mid-request would race two clients trying to construct it.
_model: WhisperModel | None = None
_model_lock: asyncio.Lock | None = None
_model_size: str | None = None


def _get_lock() -> asyncio.Lock:
    global _model_lock
    if _model_lock is None:
        _model_lock = asyncio.Lock()
    return _model_lock


async def _ensure_model(requested_size: str) -> WhisperModel:
    global _model, _model_size
    # Fast path: lock-free read for the cached-hit case so concurrent
    # requests don't serialize once the model is loaded.
    if _model is not None and _model_size == requested_size:
        return _model
    async with _get_lock():
        if _model is not None and _model_size == requested_size:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=(
                    "ASR not available on this server — install with "
                    "pip install tokenpal[audio]."
                ),
            ) from e

        device = os.environ.get("TOKENPAL_ASR_DEVICE", "cpu").lower()
        compute_type = os.environ.get(
            "TOKENPAL_ASR_COMPUTE_TYPE",
            "float16" if device == "cuda" else "int8",
        )
        log.info(
            "asr-server: loading %s on %s/%s",
            requested_size, device, compute_type,
        )
        # Run blocking load off the event loop so the request that
        # triggered it doesn't tie up FastAPI's worker.
        _model = await asyncio.to_thread(
            WhisperModel,
            requested_size,
            device=device,
            compute_type=compute_type,
        )
        _model_size = requested_size
    return _model


def _wav_bytes_to_pcm(buf: bytes) -> tuple[bytes, int]:
    """Decode a WAV upload to int16 mono PCM. Only WAV is supported on the
    server side — clients (RemoteWhisperBackend) wrap raw PCM in a WAV
    header before POST. Anything else trips a 400."""
    try:
        with wave.open(io.BytesIO(buf), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise HTTPException(
                    status_code=400,
                    detail="audio must be mono int16",
                )
            sr = wav.getframerate()
            return wav.readframes(wav.getnframes()), sr
    except wave.Error as e:
        raise HTTPException(
            status_code=400, detail=f"could not decode WAV: {e}",
        ) from e


@router.post("/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("small.en"),
    language: str = Form("en"),
    response_format: str = Form("json"),
) -> dict[str, Any]:
    """OpenAI-compatible transcription endpoint.

    Only the subset our client actually uses is wired:
    file, model, language, response_format. ``temperature``,
    ``timestamp_granularities``, ``prompt`` are accepted by ignoring
    extra form fields — FastAPI raises on unknown args otherwise.
    """
    payload = await file.read()
    pcm, sample_rate = _wav_bytes_to_pcm(payload)
    samples = pcm_int16_to_float32(pcm)

    whisper = await _ensure_model(model)
    # Run inference off the loop — beam_size=1 keeps latency tight.
    segments, _info = await asyncio.to_thread(
        whisper.transcribe,
        samples,
        language=language,
        beam_size=1,
    )
    text = "".join(seg.text for seg in segments).strip()

    if response_format == "text":
        return {"text": text}
    # OpenAI-style json: ``{"text": "..."}``. We don't return per-segment
    # timestamps yet — callers that need them can request response_format=
    # "verbose_json" once we wire it.
    return {"text": text, "language": language, "sample_rate": sample_rate}

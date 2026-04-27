"""Kokoro-onnx TTS backend (subprocess-isolated).

Synthesis runs in a child process — see ``_kokoro_worker.py`` for the
protocol — so its ONNX inference + numpy glue can't hold the parent's
GIL during a 60Hz Qt tick. Without this, dragging the buddy while a
reply was being synthesized stuttered visibly even after we collapsed
chunk-streaming into whole-utterance pre-synth.

The parent process never imports ``kokoro_onnx`` — that import lives
inside the worker. ``list_voices()`` reads ``voices-v1.0.bin`` directly
with numpy so the options dropdown works without spawning a worker.

Model files live at ``<data_dir>/audio/`` and are fetched by
``tokenpal.audio.deps.install_models()``:

    kokoro-v1.0.onnx       (fp32, ~325MB)
    kokoro-v1.0.fp16.onnx  (~177MB) — quality default
    kokoro-v1.0.int8.onnx  (~92MB)  — auto on ≤8GB RAM
    voices-v1.0.bin        (~28MB)
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar, Literal

from tokenpal.audio.base import TTSBackend, VoiceInfo
from tokenpal.audio.registry import register_tts_backend

log = logging.getLogger(__name__)

Quantization = Literal["int8", "fp16", "fp32"]

# Suffix encoded in the GitHub release filenames at
# https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
# fp32 has no suffix, fp16/int8 do. Kept here so deps.install_models() and
# the backend agree on the canonical filenames.
MODEL_FILENAMES: dict[Quantization, str] = {
    "fp32": "kokoro-v1.0.onnx",
    "fp16": "kokoro-v1.0.fp16.onnx",
    "int8": "kokoro-v1.0.int8.onnx",
}
VOICES_FILENAME = "voices-v1.0.bin"

_WORKER_MODULE = "tokenpal.audio.backends._kokoro_worker"


class _KokoroWorker:
    """Owns the subprocess Popen + IPC framing.

    All IO is synchronous (one outstanding command at a time, paired
    write/read on each call). Callers wrap synth() in run_in_executor so
    the asyncio loop stays responsive.
    """

    def __init__(self, model_path: Path, voices_path: Path) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 — sys.executable is trusted
            [
                sys.executable, "-m", _WORKER_MODULE,
                str(model_path), str(voices_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(
            target=self._drain_stderr,
            name="kokoro-worker-stderr",
            daemon=True,
        ).start()
        # Wait for the model-loaded handshake so warmup() can guarantee
        # the next synth pays no model-load cost.
        self._read_exact(4)

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                log.warning("kokoro worker: %s", line)

    def _read_exact(self, n: int) -> bytes:
        assert self._proc.stdout is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("kokoro worker died")
            buf.extend(chunk)
        return bytes(buf)

    def synth(self, text: str, voice: str, speed: float) -> bytes:
        assert self._proc.stdin is not None
        cmd = (
            json.dumps({
                "op": "synth", "text": text, "voice": voice, "speed": speed,
            }) + "\n"
        ).encode("utf-8")
        self._proc.stdin.write(cmd)
        self._proc.stdin.flush()
        (n,) = struct.unpack(">I", self._read_exact(4))
        return self._read_exact(n) if n else b""

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.write(b'{"op":"exit"}\n')
                self._proc.stdin.flush()
                self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()


@register_tts_backend("kokoro")
class KokoroBackend(TTSBackend):
    sample_rate: ClassVar[int] = 24000
    channels: ClassVar[int] = 1
    sample_format: ClassVar[Literal["float32", "int16"]] = "float32"

    def __init__(self, data_dir: Path, quantization: Quantization = "fp16") -> None:
        self._audio_dir = data_dir / "audio"
        self._quantization = quantization
        self._worker: _KokoroWorker | None = None

    @property
    def model_path(self) -> Path:
        return self._audio_dir / MODEL_FILENAMES[self._quantization]

    @property
    def voices_path(self) -> Path:
        return self._audio_dir / VOICES_FILENAME

    def models_present(self) -> bool:
        return self.model_path.exists() and self.voices_path.exists()

    def list_voices(self) -> list[VoiceInfo]:
        if not self.voices_path.exists():
            return []
        try:
            import numpy as np
            voices = np.load(self.voices_path)
            names = sorted(voices.keys())
        except Exception as e:
            log.warning("kokoro: failed to read voices file: %s", e)
            return []
        return [
            VoiceInfo(id=f"kokoro:{n}", raw=n, backend="kokoro", label=n)
            for n in names
        ]

    async def warmup(self) -> None:
        if self._worker is not None:
            return
        if not self.models_present():
            raise FileNotFoundError(
                f"Kokoro model files missing under {self._audio_dir}. "
                f"Run /voice-io install to fetch them.",
            )
        loop = asyncio.get_running_loop()
        self._worker = await loop.run_in_executor(
            None, _KokoroWorker, self.model_path, self.voices_path,
        )
        log.debug("kokoro: worker ready (%s)", self._quantization)

    async def synthesize(
        self, text: str, voice_id: str, *, speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if self._worker is None:
            await self.warmup()
        assert self._worker is not None
        raw = voice_id.removeprefix("kokoro:")
        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(
            None, self._worker.synth, text, raw, speed,
        )
        if pcm:
            yield pcm

    async def aclose(self) -> None:
        if self._worker is None:
            return
        worker = self._worker
        self._worker = None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, worker.close)

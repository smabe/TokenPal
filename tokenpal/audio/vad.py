"""Silero VAD via onnxruntime — no torch dep.

Wraps the pre-exported silero_vad.onnx (~1.6MB). The pip package
silero-vad pulls torch (~600MB) we don't need elsewhere; the onnx
model + manual hidden-state management is functionally equivalent.

ONNX contract (opset 16):
  inputs:  input float32 [1, 512] | state float32 [2,1,128] | sr int64
  outputs: prob  float32 [1, 1]   | stateN float32 [2,1,128]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

from tokenpal.audio.util import SAMPLE_RATE_HZ, VAD_CHUNK_SAMPLES

if TYPE_CHECKING:
    import onnxruntime as ort

log = logging.getLogger(__name__)

VAD_MODEL_FILENAME: Final[str] = "silero_vad.onnx"

# Re-exports for callers that import from vad directly.
SAMPLE_RATE = SAMPLE_RATE_HZ
CHUNK_SAMPLES = VAD_CHUNK_SAMPLES

_STATE_SHAPE: Final[tuple[int, int, int]] = (2, 1, 128)


class VadEvent(StrEnum):
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"


@dataclass
class SileroVAD:
    data_dir: Path
    threshold: float = 0.5
    min_silence_s: float = 0.7
    min_speech_s: float = 0.05

    def __post_init__(self) -> None:
        self._session: ort.InferenceSession | None = None
        self._state: np.ndarray | None = None
        self._sr_input: np.ndarray | None = None
        self._in_speech: bool = False
        self._speech_run_s: float = 0.0
        self._silence_run_s: float = 0.0

    @property
    def model_path(self) -> Path:
        return self.data_dir / "audio" / "vad" / VAD_MODEL_FILENAME

    def models_present(self) -> bool:
        return self.model_path.exists()

    async def warmup(self) -> None:
        if self._session is not None:
            return
        if not self.models_present():
            raise FileNotFoundError(
                f"Silero VAD model missing at {self.model_path}. "
                f"Run /voice-io install to fetch it.",
            )
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"],
        )
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._sr_input = np.array(SAMPLE_RATE, dtype=np.int64)
        log.debug("silero-vad: warmed up (threshold=%.2f)", self.threshold)

    def reset(self) -> None:
        if self._state is not None:
            self._state.fill(0.0)
        self._in_speech = False
        self._speech_run_s = 0.0
        self._silence_run_s = 0.0

    def process(self, frame: bytes) -> VadEvent | None:
        if self._session is None or self._state is None:
            return None

        # One float conversion for the whole frame, then slice. Trailing
        # partial frames are dropped — 80ms FSM frames divide cleanly into
        # 512 samples at 16kHz.
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        n_chunks = samples.size // CHUNK_SAMPLES
        if n_chunks == 0:
            return None

        emitted: VadEvent | None = None
        chunk_duration_s = CHUNK_SAMPLES / SAMPLE_RATE
        for i in range(n_chunks):
            start = i * CHUNK_SAMPLES
            chunk = samples[start : start + CHUNK_SAMPLES].reshape(1, -1)
            outputs = self._session.run(
                None,
                {
                    "input": chunk,
                    "state": self._state,
                    "sr": self._sr_input,
                },
            )
            prob = float(outputs[0][0][0])
            self._state = outputs[1]
            event = self._update_hysteresis(prob, chunk_duration_s)
            if event is not None:
                emitted = event
        return emitted

    def _update_hysteresis(
        self, prob: float, chunk_s: float,
    ) -> VadEvent | None:
        is_speech = prob >= self.threshold
        if self._in_speech:
            if is_speech:
                self._silence_run_s = 0.0
                return None
            self._silence_run_s += chunk_s
            if self._silence_run_s >= self.min_silence_s:
                self._in_speech = False
                self._silence_run_s = 0.0
                self._speech_run_s = 0.0
                return VadEvent.SPEECH_ENDED
            return None
        if not is_speech:
            self._speech_run_s = 0.0
            return None
        self._speech_run_s += chunk_s
        if self._speech_run_s >= self.min_speech_s:
            self._in_speech = True
            self._speech_run_s = 0.0
            self._silence_run_s = 0.0
            return VadEvent.SPEECH_STARTED
        return None

    async def aclose(self) -> None:
        self._session = None
        self._state = None

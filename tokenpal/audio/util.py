"""Small shared helpers for the audio path.

Kept tiny on purpose — heavy work (model loading, streams) lives in the
backend modules. numpy is imported here unconditionally, so don't import
``util`` from anywhere on the ambient-only boot path.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# Common audio params. Spread across multiple modules previously, which
# risked drift. Single source.
SAMPLE_RATE_HZ = 16000
WAKE_FRAME_SAMPLES = 1280   # 80ms @ 16kHz, openwakeword's recommended size
VAD_CHUNK_SAMPLES = 512     # silero-vad's required chunk size at 16kHz


def pcm_int16_to_float32(pcm: bytes) -> NDArray[np.float32]:
    """int16 PCM bytes → float32 ndarray normalized to [-1, 1].

    The unit conversion both Whisper and Silero want.
    """
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

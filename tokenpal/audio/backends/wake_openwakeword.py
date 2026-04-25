"""OpenWakeWord backend.

Filename starts with ``wake_`` so the registry's discover_backends()
gate skips importing this module on ambient-only boots —
``include_input=False`` is what keeps openwakeword off ambient narration.

openwakeword needs three model files at runtime:
  * a per-wakeword .onnx (e.g. ``hey_jarvis_v0.1.onnx``,
    or our future ``hey_tokenpal.onnx``)
  * a shared ``melspectrogram.onnx``
  * a shared ``embedding_model.onnx``

The shared two are downloaded by ``openwakeword.utils.download_models``
into the package's ``resources/models/`` dir on first use; the
per-wakeword .onnx is what install_models() will fetch into
``<data_dir>/audio/wakeword/``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from tokenpal.audio.base import WakeEvent, WakeWordBackend
from tokenpal.audio.registry import register_wakeword_backend

if TYPE_CHECKING:
    from openwakeword.model import Model as _OWWModel

log = logging.getLogger(__name__)

# Skip predict() when loudest sample is below this — ~-40dBFS, quiet
# enough that the model would just run on noise floor.
_VOLUME_GATE = 200


@register_wakeword_backend("openwakeword")
class OpenWakeWordBackend(WakeWordBackend):
    sample_rate: ClassVar[int] = 16000
    chunk_samples: ClassVar[int] = 1280

    def __init__(
        self,
        data_dir: Path,
        model_name: str = "hey_jarvis",
        threshold: float = 0.7,
    ) -> None:
        # Where install_models() drops the per-wakeword .onnx. The shared
        # melspectrogram + embedding files live under the openwakeword pip
        # package itself; download_models() places them.
        self._wake_dir = data_dir / "audio" / "wakeword"
        self._model_name = model_name
        self._threshold = threshold
        self._model: _OWWModel | None = None

    @property
    def model_path(self) -> Path:
        # openwakeword's stock models are named e.g. ``hey_jarvis_v0.1.onnx``;
        # our trained model would be ``hey_tokenpal.onnx``. The model_name
        # config ("hey_jarvis", "hey_tokenpal") maps to the filename via
        # this glob — picks the first .onnx whose stem starts with the
        # configured name.
        if not self._wake_dir.exists():
            return self._wake_dir / f"{self._model_name}.onnx"
        for path in sorted(self._wake_dir.glob(f"{self._model_name}*.onnx")):
            return path
        return self._wake_dir / f"{self._model_name}.onnx"

    def models_present(self) -> bool:
        return self.model_path.exists()

    async def warmup(self) -> None:
        if self._model is not None:
            return
        if not self.models_present():
            raise FileNotFoundError(
                f"OpenWakeWord model {self._model_name!r} missing under "
                f"{self._wake_dir}. Run /voice-io install to fetch it.",
            )

        # Lazy import — top-level import would trip the modularity
        # contract for any caller that walked the backends package.
        from openwakeword.model import Model

        # openwakeword renamed the constructor kwarg between versions:
        # older builds use ``wakeword_model_paths=``, current uses
        # ``wakeword_models=``. Try current first; TypeError → legacy.
        try:
            self._model = Model(
                wakeword_models=[str(self.model_path)],
                inference_framework="onnx",
            )
        except TypeError:
            self._model = Model(
                wakeword_model_paths=[str(self.model_path)],
                inference_framework="onnx",
            )
        log.debug(
            "openwakeword: warmed up %s (threshold=%.2f)",
            self._model_name, self._threshold,
        )

    def detect(self, frame: bytes) -> WakeEvent | None:
        if self._model is None:
            return None
        samples = np.frombuffer(frame, dtype=np.int16)
        # Volume gate: int16 max abs in quiet rooms is well under 200.
        # openwakeword's mel + embedding pass costs CPU we save here.
        if int(np.abs(samples).max()) < _VOLUME_GATE:
            return None
        scores: dict[str, float] = self._model.predict(samples)
        top_name, top_score = max(scores.items(), key=lambda kv: kv[1])
        if top_score >= self._threshold:
            return WakeEvent(model_name=top_name, score=float(top_score))
        return None

    async def aclose(self) -> None:
        # openwakeword.Model holds onnxruntime sessions; dropping the ref
        # frees them on GC, same pattern as KokoroBackend.
        self._model = None

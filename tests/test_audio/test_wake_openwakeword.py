"""OpenWakeWordBackend contract tests.

Like the Kokoro suite, these cover the surface that doesn't need real
weights: filename resolution, models-present check, volume gate logic,
threshold comparison. Real predict() against onnx weights is a manual
smoke + the --validate audio check.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tokenpal.audio.backends.wake_openwakeword import (
    _VOLUME_GATE,
    OpenWakeWordBackend,
)
from tokenpal.audio.base import WakeEvent
from tokenpal.audio.registry import (
    discover_backends,
    get_wakeword_backend,
    registered_wakeword_backends,
)


def _force_input_side_discovery() -> None:
    # The default discover walk skips wake_* and asr_* — pull this module
    # in explicitly via include_input=True so the decorator fires.
    discover_backends(include_input=True)


def test_backend_registered_under_openwakeword() -> None:
    _force_input_side_discovery()
    assert "openwakeword" in registered_wakeword_backends()
    assert get_wakeword_backend("openwakeword") is OpenWakeWordBackend


def test_models_present_false_on_fresh_dir(tmp_path: Path) -> None:
    b = OpenWakeWordBackend(tmp_path, model_name="hey_jarvis")
    assert b.models_present() is False


def test_model_path_picks_first_versioned_match(tmp_path: Path) -> None:
    wake_dir = tmp_path / "audio" / "wakeword"
    wake_dir.mkdir(parents=True)
    # Stock openwakeword names files ``<wake>_v0.1.onnx``; the backend
    # should resolve "hey_jarvis" to the actual file regardless of suffix.
    (wake_dir / "hey_jarvis_v0.1.onnx").write_bytes(b"x")
    b = OpenWakeWordBackend(tmp_path, model_name="hey_jarvis")
    assert b.models_present() is True
    assert b.model_path.name == "hey_jarvis_v0.1.onnx"


async def test_warmup_raises_when_model_missing(tmp_path: Path) -> None:
    b = OpenWakeWordBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        await b.warmup()


def test_detect_returns_none_when_not_warm(tmp_path: Path) -> None:
    b = OpenWakeWordBackend(tmp_path)
    # 1280 samples of int16 silence — empty bytes is fine since the gate
    # short-circuits before it cares.
    assert b.detect(b"\x00\x00" * 1280) is None


def test_detect_volume_gate_skips_quiet_frame(tmp_path: Path) -> None:
    b = OpenWakeWordBackend(tmp_path)
    # Sneak in a stand-in model so we can prove predict() is NOT called.
    fake_model = mock.MagicMock()
    fake_model.predict.return_value = {"hey_jarvis": 0.9}
    b._model = fake_model

    # All samples below the volume gate.
    quiet = (b"\x10\x00" * 1280)  # 16 < _VOLUME_GATE
    assert b.detect(quiet) is None
    fake_model.predict.assert_not_called()


def test_detect_emits_event_above_threshold(tmp_path: Path) -> None:
    import numpy as np

    b = OpenWakeWordBackend(tmp_path, threshold=0.7)
    fake_model = mock.MagicMock()
    fake_model.predict.return_value = {"hey_jarvis": 0.91, "alexa": 0.10}
    b._model = fake_model

    # Make every sample loud enough to pass the gate.
    loud = (np.ones(1280, dtype=np.int16) * (_VOLUME_GATE * 2)).tobytes()
    event = b.detect(loud)
    assert event == WakeEvent(model_name="hey_jarvis", score=pytest.approx(0.91))


def test_detect_below_threshold_returns_none(tmp_path: Path) -> None:
    import numpy as np

    b = OpenWakeWordBackend(tmp_path, threshold=0.7)
    fake_model = mock.MagicMock()
    fake_model.predict.return_value = {"hey_jarvis": 0.40}
    b._model = fake_model

    loud = (np.ones(1280, dtype=np.int16) * (_VOLUME_GATE * 2)).tobytes()
    assert b.detect(loud) is None

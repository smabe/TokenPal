"""Model-file install path: missing-detection + atomic download.

Network-fetching is mocked at urllib.request.urlopen so CI doesn't pull
325MB across the wire to assert behavior we can prove with a dummy stream.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

import pytest

from tokenpal.audio import deps


def test_missing_models_lists_both_files_on_fresh_dir(tmp_path: Path) -> None:
    missing = deps.missing_models(tmp_path, quantization="fp16")
    names = sorted(p.name for p in missing)
    assert names == ["kokoro-v1.0.fp16.onnx", "voices-v1.0.bin"]


def test_missing_models_quantization_drives_filename(tmp_path: Path) -> None:
    int8 = deps.missing_models(tmp_path, quantization="int8")
    assert any(p.name == "kokoro-v1.0.int8.onnx" for p in int8)
    fp32 = deps.missing_models(tmp_path, quantization="fp32")
    assert any(p.name == "kokoro-v1.0.onnx" for p in fp32)


def test_missing_models_unknown_quantization_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        deps.missing_models(tmp_path, quantization="bogus")


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def test_install_models_downloads_atomically(tmp_path: Path) -> None:
    payload = b"x" * ((1 << 20) + 5)  # >1MB so the chunk loop runs more than once

    def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload)

    with mock.patch.object(deps.urllib.request, "urlopen", side_effect=fake_urlopen):
        result = deps.install_models(tmp_path, quantization="fp16")

    assert result.ok, result.message
    assert (tmp_path / "audio" / "kokoro-v1.0.fp16.onnx").exists()
    assert (tmp_path / "audio" / "voices-v1.0.bin").exists()
    # No tmp leftovers — atomicity guarantee.
    assert list((tmp_path / "audio").glob("*.tmp")) == []


def test_install_models_idempotent_when_present(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "kokoro-v1.0.fp16.onnx").write_bytes(b"already-here")
    (audio / "voices-v1.0.bin").write_bytes(b"already-here")
    # urlopen would raise if called — proves no network hit on the happy path.
    with mock.patch.object(
        deps.urllib.request, "urlopen", side_effect=AssertionError("no fetch"),
    ):
        result = deps.install_models(tmp_path, quantization="fp16")
    assert result.ok
    assert "already present" in result.message


def test_missing_input_models_lists_vad_and_wakeword(tmp_path: Path) -> None:
    missing = deps.missing_input_models(tmp_path)
    names = sorted(p.name for p in missing)
    assert "silero_vad.onnx" in names
    assert "hey_jarvis_v0.1.onnx" in names
    assert "melspectrogram.onnx" in names
    assert "embedding_model.onnx" in names


def test_install_input_models_downloads_each_file(tmp_path: Path) -> None:
    payload = b"x" * ((1 << 20) + 5)

    def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload)

    with mock.patch.object(deps.urllib.request, "urlopen", side_effect=fake_urlopen):
        result = deps.install_input_models(tmp_path)
    assert result.ok, result.message
    assert (tmp_path / "audio" / "vad" / "silero_vad.onnx").exists()
    wake_dir = tmp_path / "audio" / "wakeword"
    assert (wake_dir / "hey_jarvis_v0.1.onnx").exists()
    assert (wake_dir / "melspectrogram.onnx").exists()
    assert (wake_dir / "embedding_model.onnx").exists()


def test_install_input_models_idempotent(tmp_path: Path) -> None:
    # Pre-create all files; urlopen-as-bomb proves no network fetch ran.
    audio = tmp_path / "audio"
    (audio / "vad").mkdir(parents=True)
    (audio / "wakeword").mkdir()
    (audio / "vad" / "silero_vad.onnx").write_bytes(b"x")
    for name in ("hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
        (audio / "wakeword" / name).write_bytes(b"x")
    with mock.patch.object(
        deps.urllib.request, "urlopen", side_effect=AssertionError("no fetch"),
    ):
        result = deps.install_input_models(tmp_path)
    assert result.ok
    assert "already present" in result.message

"""--validate audio readiness check.

Asserts the report's behavior at the toggle boundary: with both [audio]
toggles off, _check_audio is a silent no-op (no problems, no noise). With
ambient on but install missing, two problems surface: deps + models.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from tokenpal.cli import _check_audio
from tokenpal.config.schema import AudioConfig, PathsConfig, TokenPalConfig


def _config(audio: AudioConfig, data_dir: Path) -> TokenPalConfig:
    cfg = TokenPalConfig()
    cfg.audio = audio
    cfg.paths = PathsConfig(data_dir=str(data_dir))
    return cfg


def test_check_audio_skips_when_both_toggles_off(
    tmp_path: Path, capsys,
) -> None:
    cfg = _config(AudioConfig(), tmp_path)
    assert _check_audio(cfg) == 0
    out = capsys.readouterr().out
    assert "Audio I/O" not in out


def test_check_audio_warns_on_missing_deps_and_models(
    tmp_path: Path, capsys,
) -> None:
    cfg = _config(AudioConfig(speak_ambient_enabled=True), tmp_path)
    # Force the deps check to report missing wheels regardless of host venv.
    with mock.patch(
        "tokenpal.audio.deps.missing_deps",
        return_value=("kokoro-onnx", "sounddevice"),
    ):
        problems = _check_audio(cfg)
    out = capsys.readouterr().out
    # Two distinct issues: pip wheels + model files.
    assert problems == 2
    assert "missing deps" in out
    assert "missing model files" in out


def test_check_audio_passes_when_everything_present(
    tmp_path: Path, capsys,
) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"x")
    (audio_dir / "voices-v1.0.bin").write_bytes(b"x")
    cfg = _config(AudioConfig(speak_ambient_enabled=True), tmp_path)
    with mock.patch(
        "tokenpal.audio.deps.missing_deps",
        return_value=(),
    ):
        problems = _check_audio(cfg)
    out = capsys.readouterr().out
    assert problems == 0
    assert "audio wheels installed" in out
    assert "kokoro models present (fp16)" in out

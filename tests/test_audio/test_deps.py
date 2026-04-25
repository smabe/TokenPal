"""Tests for tokenpal/audio/deps.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tokenpal.audio import deps as audio_deps
from tokenpal.audio.deps import (
    AUDIO_DEPS,
    format_warning,
    install,
    missing_deps,
)


@contextmanager
def _patch_find_spec(present: set[str]) -> Iterator[None]:
    """Pretend ``import_name`` is installed iff it's in ``present``."""
    real = importlib.util.find_spec

    def fake(name: str, package: Any = None) -> Any:
        if name in set(AUDIO_DEPS.values()):
            return object() if name in present else None
        return real(name, package)

    audio_deps.importlib.util.find_spec = fake  # type: ignore[assignment]
    try:
        yield
    finally:
        audio_deps.importlib.util.find_spec = real  # type: ignore[assignment]


# Order pinned to AUDIO_DEPS dict insertion order in tokenpal.audio.deps.
_ALL_PIP = ("kokoro-onnx", "sounddevice", "openwakeword", "faster-whisper")
_ALL_IMPORT = {"kokoro_onnx", "sounddevice", "openwakeword", "faster_whisper"}


def test_missing_deps_when_nothing_installed() -> None:
    with _patch_find_spec(present=set()):
        assert missing_deps() == _ALL_PIP


def test_missing_deps_when_partial() -> None:
    # Output side present, input side missing — covers the realistic phase 2b
    # → phase 3 upgrade path.
    with _patch_find_spec(present={"kokoro_onnx", "sounddevice"}):
        assert missing_deps() == ("openwakeword", "faster-whisper")


def test_missing_deps_when_all_present() -> None:
    with _patch_find_spec(present=_ALL_IMPORT):
        assert missing_deps() == ()


def test_missing_deps_include_input_false_skips_input_side() -> None:
    # Ambient-only callers must not probe input-side wheels — find_spec
    # would otherwise route through the modularity blocker. With output
    # side present and input side missing, the gated call is empty.
    with _patch_find_spec(present={"kokoro_onnx", "sounddevice"}):
        assert missing_deps(include_input=False) == ()
        assert missing_deps(include_input=True) == (
            "openwakeword", "faster-whisper",
        )


def test_install_short_circuits_when_already_installed() -> None:
    with _patch_find_spec(present=_ALL_IMPORT):
        result = install()
    assert result.ok is True
    assert "already" in result.message.lower()


def test_install_runs_pip_for_missing_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(audio_deps.subprocess, "run", fake_run)

    # Pretend kokoro-onnx is missing on first check (pre-install) AND
    # present on the second check (post-install verification). The other
    # deps stay present throughout. The fake never falls through to the
    # real importlib.util.find_spec — monkeypatch replaces it on the same
    # module reference, so a fallthrough would recurse infinitely.
    calls = {"n": 0}
    present = {"sounddevice", "openwakeword", "faster_whisper"}

    def fake_find_spec(name: str, package: Any = None) -> Any:
        if name in present:
            return object()
        if name == "kokoro_onnx":
            calls["n"] += 1
            return None if calls["n"] == 1 else object()
        return None

    monkeypatch.setattr(audio_deps.importlib.util, "find_spec", fake_find_spec)

    result = install()
    assert result.ok is True
    assert captured["cmd"] == [
        sys.executable, "-m", "pip", "install",
        "--progress-bar", "off", "-q",
        "kokoro-onnx",
    ]


def test_install_reports_pip_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="ERROR: No matching distribution\n",
        )

    monkeypatch.setattr(audio_deps.subprocess, "run", fake_run)
    monkeypatch.setattr(
        audio_deps.importlib.util,
        "find_spec",
        lambda name, package=None: None,
    )

    result = install()
    assert result.ok is False
    assert "exit 1" in result.message
    assert "no matching distribution" in result.message.lower()


def test_install_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(audio_deps.subprocess, "run", fake_run)
    monkeypatch.setattr(
        audio_deps.importlib.util,
        "find_spec",
        lambda name, package=None: None,
    )

    result = install(timeout_s=5.0)
    assert result.ok is False
    assert "timed out" in result.message


def test_install_reports_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(audio_deps.subprocess, "run", fake_run)
    monkeypatch.setattr(
        audio_deps.importlib.util,
        "find_spec",
        lambda name, package=None: None,
    )

    result = install()
    assert result.ok is False
    assert "cancelled" in result.message


def test_format_warning_returns_none_when_all_present() -> None:
    with _patch_find_spec(present=_ALL_IMPORT):
        assert format_warning() is None


def test_format_warning_lists_missing_pip_names() -> None:
    with _patch_find_spec(present={"sounddevice"}):
        msg = format_warning()
    assert msg is not None
    assert "kokoro-onnx" in msg
    assert "/voice-io install" in msg


def test_format_warning_honors_prefix() -> None:
    with _patch_find_spec(present=set()):
        msg = format_warning(prefix="audio deps missing")
    assert msg is not None
    assert msg.startswith("audio deps missing:")


def test_install_reports_post_install_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pip claimed success but the import still fails — surface it
    instead of silently lying."""
    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(audio_deps.subprocess, "run", fake_run)
    monkeypatch.setattr(
        audio_deps.importlib.util,
        "find_spec",
        lambda name, package=None: None,
    )

    result = install()
    assert result.ok is False
    assert "still missing" in result.message
    assert "restart" in result.message.lower()



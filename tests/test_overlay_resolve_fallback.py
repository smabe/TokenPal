"""Silent headless fallback for ``[ui] overlay = "qt"``.

When Qt can't run on the host — PySide6 missing, TOKENPAL_HEADLESS=1,
or no DISPLAY on Linux — ``resolve_overlay`` should log at INFO and
fall back to the Textual overlay instead of crashing.
"""

from __future__ import annotations

import logging

import pytest

from tokenpal.ui.registry import (
    _OVERLAY_REGISTRY,
    _qt_unavailable_reason,
    discover_overlays,
    resolve_overlay,
)


@pytest.fixture(autouse=True)
def _discovered() -> None:
    discover_overlays()


def test_fallback_when_qt_not_in_registry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """If PySide6 isn't importable, qt never got registered — a user
    who set `overlay = "qt"` in config should get textual, not a crash."""
    monkeypatch.setitem(_OVERLAY_REGISTRY, "qt", _OVERLAY_REGISTRY["qt"])
    # Simulate qt missing by pulling it out of the registry transiently.
    qt_cls = _OVERLAY_REGISTRY.pop("qt")
    try:
        with caplog.at_level(logging.INFO, logger="tokenpal.ui.registry"):
            overlay = resolve_overlay({"overlay": "qt"})
        assert overlay.overlay_name == "textual"
        assert any(
            "qt overlay unavailable" in rec.message for rec in caplog.records
        )
    finally:
        _OVERLAY_REGISTRY["qt"] = qt_cls


def test_fallback_when_tokenpal_headless_env_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("TOKENPAL_HEADLESS", "1")
    with caplog.at_level(logging.INFO, logger="tokenpal.ui.registry"):
        overlay = resolve_overlay({"overlay": "qt"})
    assert overlay.overlay_name == "textual"
    assert any("TOKENPAL_HEADLESS" in rec.message for rec in caplog.records)


def test_qt_unavailable_reason_reports_specific_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKENPAL_HEADLESS", "1")
    assert _qt_unavailable_reason() == "TOKENPAL_HEADLESS=1"
    monkeypatch.delenv("TOKENPAL_HEADLESS", raising=False)
    # With qt registered and no headless env, Linux with no DISPLAY
    # should still flag a reason. macOS/Windows always have a display.
    from tokenpal.util.platform import current_platform
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if current_platform() == "linux":
        assert _qt_unavailable_reason() is not None
    else:
        assert _qt_unavailable_reason() is None


def test_qt_overlay_still_resolves_when_available() -> None:
    """Baseline: with Qt installed and no fallback trigger, "qt" resolves
    to QtOverlay."""
    overlay = resolve_overlay({"overlay": "qt"})
    assert overlay.overlay_name == "qt"


def test_unknown_overlay_still_raises_clean_error() -> None:
    """Fallback path must not swallow genuinely-bad config — a typo
    like `overlay = "qtt"` should still explode noisily."""
    with pytest.raises(RuntimeError, match="Unknown overlay"):
        resolve_overlay({"overlay": "qtt"})

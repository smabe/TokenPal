"""Tests for the rage / frustration detector.

Also asserts at source level that the module never imports anything from
tokenpal.senses._keyboard_bus (mirrors typing_cadence's no-keys invariant
but at the consumer boundary — rage detection operates on aggregated
SenseReadings, never raw keystrokes).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tokenpal.brain.rage_detector import RageDetector, RageSignal
from tokenpal.config.schema import RageDetectConfig
from tokenpal.senses.base import SenseReading


def _typing(bucket: str) -> SenseReading:
    return SenseReading(
        sense_name="typing_cadence",
        timestamp=time.monotonic(),
        data={"bucket": bucket},
        summary=f"typing {bucket}",
        changed_from="",
    )


def _app(name: str, changed: bool = True) -> SenseReading:
    return SenseReading(
        sense_name="app_awareness",
        timestamp=time.monotonic(),
        data={"app_name": name},
        summary=f"App: {name}",
        changed_from="switched from Editor" if changed else "",
    )


@pytest.fixture()
def config() -> RageDetectConfig:
    return RageDetectConfig(
        enabled=True,
        distraction_apps=["twitter", "reddit", "youtube"],
        rage_post_pause_min_s=0.01,
        rage_post_pause_max_s=2.0,
        rage_burst_recency_s=60.0,
        cooldown_s=60.0,
    )


# ---------------------------------------------------------------------


def test_pattern_triggers(config: RageDetectConfig) -> None:
    d = RageDetector(config=config)
    # burst -> pause -> distraction switch
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])  # pause starts here
    time.sleep(0.02)             # > rage_post_pause_min_s
    signal = d.ingest([_app("Twitter")])
    assert isinstance(signal, RageSignal)
    assert "Twitter" in signal.app_name
    assert signal.pause_s >= 0.01


def test_disabled_detector_never_fires(config: RageDetectConfig) -> None:
    config.enabled = False
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.02)
    assert d.ingest([_app("Twitter")]) is None


def test_non_distraction_app_does_not_fire(config: RageDetectConfig) -> None:
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.02)
    assert d.ingest([_app("VS Code")]) is None


def test_pause_too_short_does_not_fire(config: RageDetectConfig) -> None:
    config.rage_post_pause_min_s = 1.0
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    # Immediate switch; pause is basically zero
    assert d.ingest([_app("Twitter")]) is None


def test_pause_too_long_does_not_fire(config: RageDetectConfig) -> None:
    config.rage_post_pause_max_s = 0.05
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.2)  # pause exceeds max
    assert d.ingest([_app("Twitter")]) is None


def test_no_burst_no_fire(config: RageDetectConfig) -> None:
    """User was never in rapid/furious — just idle → distraction switch."""
    d = RageDetector(config=config)
    d.ingest([_typing("idle")])
    assert d.ingest([_app("Twitter")]) is None


def test_cooldown_enforced(config: RageDetectConfig) -> None:
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.02)
    first = d.ingest([_app("Twitter")])
    assert first is not None
    d.mark_emitted()
    # Rebuild the pattern and try again
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.02)
    second = d.ingest([_app("Reddit")])
    assert second is None


def test_app_transition_flag_required(config: RageDetectConfig) -> None:
    """Steady-state polls of the distraction app shouldn't trigger — only
    the first reading after a switch does (changed_from is non-empty)."""
    d = RageDetector(config=config)
    d.ingest([_typing("rapid")])
    d.ingest([_typing("idle")])
    time.sleep(0.02)
    # First switch-in fires
    first = d.ingest([_app("Twitter")])
    assert first is not None
    d.mark_emitted()
    # Subsequent steady-state polls never fire again without a fresh pattern
    assert d.ingest([_app("Twitter", changed=False)]) is None


# ---------------------------------------------------------------------
# Source invariant: rage_detector never imports from _keyboard_bus
# ---------------------------------------------------------------------


def test_rage_detector_does_not_touch_keyboard_bus() -> None:
    """Parse imports only — the docstring references _keyboard_bus as
    context for why this invariant matters, but the module itself must
    never actually import keystroke-level infrastructure."""
    import ast

    source_path = Path("tokenpal/brain/rage_detector.py")
    tree = ast.parse(source_path.read_text())
    forbidden = {"_keyboard_bus", "pynput"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name for f in forbidden), (
                    f"forbidden import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not any(f in node.module for f in forbidden), (
                f"forbidden from-import: {node.module}"
            )

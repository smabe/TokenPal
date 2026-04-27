"""Tests for IntentStore — set/get/clear + drift detection."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tokenpal.brain.intent import DriftSignal, IntentError, IntentStore
from tokenpal.brain.memory import MemoryStore
from tokenpal.config.schema import IntentConfig


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "m.db")
    s.setup()
    return s


@pytest.fixture()
def config() -> IntentConfig:
    # Tight timers keep drift tests fast.
    return IntentConfig(
        distraction_apps=["twitter", "reddit", "youtube"],
        drift_min_dwell_s=0.01,
        drift_cooldown_s=0.05,
        max_age_s=3600.0,
    )


# -----------------------------------------------------------------------
# Intent CRUD
# -----------------------------------------------------------------------


def test_set_get_clear_round_trip(memory: MemoryStore, config: IntentConfig) -> None:
    store = IntentStore(memory=memory, config=config)
    assert store.get_active() is None
    active = store.set("finish the auth PR")
    assert active.text == "finish the auth PR"
    fetched = store.get_active()
    assert fetched is not None
    assert fetched.text == "finish the auth PR"
    assert store.clear() is True
    assert store.get_active() is None
    assert store.clear() is False


def test_set_replaces_prior_intent(memory: MemoryStore, config: IntentConfig) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("first intent")
    store.set("second intent")
    active = store.get_active()
    assert active is not None
    assert active.text == "second intent"


def test_sensitive_intent_rejected(memory: MemoryStore, config: IntentConfig) -> None:
    store = IntentStore(memory=memory, config=config)
    with pytest.raises(IntentError):
        store.set("rotate my 1Password vault keys")
    assert store.get_active() is None


def test_empty_intent_rejected(memory: MemoryStore, config: IntentConfig) -> None:
    store = IntentStore(memory=memory, config=config)
    with pytest.raises(IntentError):
        store.set("   ")


def test_expired_intent_not_returned_by_get_active(
    memory: MemoryStore, config: IntentConfig
) -> None:
    """Intents older than max_age_s are ignored by get_active() but still
    visible to get_raw() so /intent status can display them."""
    config.max_age_s = 0.001
    store = IntentStore(memory=memory, config=config)
    store.set("finish something")
    time.sleep(0.01)
    assert store.get_active() is None
    raw = store.get_raw()
    assert raw is not None
    assert raw.text == "finish something"


def test_stale_intent_notice(memory: MemoryStore, config: IntentConfig) -> None:
    store = IntentStore(memory=memory, config=config)
    assert store.stale_intent_notice() is None

    store.set("call the ford dealer")
    assert store.stale_intent_notice() is None

    config.max_age_s = 0.001
    time.sleep(0.01)
    notice = store.stale_intent_notice()
    assert notice is not None
    assert "call the ford dealer" in notice
    assert "/intent clear" in notice

    store.clear()
    assert store.stale_intent_notice() is None


# -----------------------------------------------------------------------
# Drift detection
# -----------------------------------------------------------------------


def test_drift_fires_when_conditions_met(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("finish the auth PR")
    store.on_app_change("Twitter")
    time.sleep(0.02)  # > drift_min_dwell_s (0.01)
    signal = store.check_drift()
    assert isinstance(signal, DriftSignal)
    assert signal.intent_text == "finish the auth PR"
    assert "Twitter" in signal.app_name


def test_drift_requires_active_intent(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.on_app_change("Twitter")
    time.sleep(0.02)
    assert store.check_drift() is None


def test_drift_requires_distraction_app(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("finish work")
    store.on_app_change("VS Code")
    time.sleep(0.02)
    assert store.check_drift() is None


def test_drift_requires_min_dwell(
    memory: MemoryStore, config: IntentConfig
) -> None:
    config.drift_min_dwell_s = 10.0  # longer than we'll wait
    store = IntentStore(memory=memory, config=config)
    store.set("finish work")
    store.on_app_change("Twitter")
    # No sleep — dwell is effectively 0
    assert store.check_drift() is None


def test_drift_cooldown_enforced(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("finish work")
    store.on_app_change("Twitter")
    time.sleep(0.02)
    first = store.check_drift()
    assert first is not None
    store.mark_drift_emitted()
    second = store.check_drift()
    assert second is None, "cooldown should suppress the second check"


def test_on_app_change_resets_dwell(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("finish work")
    store.on_app_change("Twitter")
    time.sleep(0.02)
    # Switch away then back — dwell restarts on each switch
    store.on_app_change("VS Code")
    store.on_app_change("Twitter")
    # No sleep after the second Twitter switch
    assert store.check_drift() is None


def test_distraction_match_is_case_insensitive_substring(
    memory: MemoryStore, config: IntentConfig
) -> None:
    store = IntentStore(memory=memory, config=config)
    store.set("finish work")
    # "YouTube" should match the "youtube" entry
    store.on_app_change("YouTube")
    time.sleep(0.02)
    assert store.check_drift() is not None

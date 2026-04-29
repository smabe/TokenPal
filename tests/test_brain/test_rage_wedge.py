from __future__ import annotations

from unittest.mock import Mock

import pytest

from tokenpal.brain.rage_detector import RageSignal
from tokenpal.brain.wedge import EmissionCandidate, PromptContext
from tokenpal.brain.wedges.rage import RageWedge
from tokenpal.config.schema import RageDetectConfig
from tokenpal.senses.base import SenseReading


def _typing(bucket: str, ts: float = 0.0) -> SenseReading:
    return SenseReading(
        sense_name="typing_cadence",
        timestamp=ts,
        data={"bucket": bucket},
        summary=f"typing {bucket}",
        changed_from="",
    )


def _app(name: str, ts: float = 0.0) -> SenseReading:
    return SenseReading(
        sense_name="app_awareness",
        timestamp=ts,
        data={"app_name": name},
        summary=f"App: {name}",
        changed_from="switched from Editor",
    )


@pytest.fixture()
def config() -> RageDetectConfig:
    return RageDetectConfig(
        enabled=True,
        distraction_apps=["twitter"],
        rage_post_pause_min_s=0.01,
        rage_post_pause_max_s=2.0,
        rage_burst_recency_s=60.0,
        cooldown_s=60.0,
    )


def _trigger_pattern(
    wedge: RageWedge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "tokenpal.brain.rage_detector.time.monotonic", lambda: clock["now"],
    )
    wedge.ingest([_typing("rapid")])
    clock["now"] += 0.5
    wedge.ingest([_typing("idle")])
    clock["now"] += 0.5
    wedge.ingest([_app("twitter")])


def test_propose_returns_none_without_pattern(config: RageDetectConfig) -> None:
    w = RageWedge(config=config)
    w.ingest([_typing("steady")])
    assert w.propose() is None


def test_propose_returns_candidate_after_pattern(
    config: RageDetectConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = RageWedge(config=config)
    _trigger_pattern(w, monkeypatch)
    cand = w.propose()
    assert isinstance(cand, EmissionCandidate)
    assert cand.wedge_name == "rage"
    assert isinstance(cand.payload, RageSignal)
    assert cand.payload.app_name == "twitter"


def test_on_emitted_clears_pending_and_arms_cooldown(
    config: RageDetectConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = RageWedge(config=config)
    _trigger_pattern(w, monkeypatch)
    cand = w.propose()
    assert cand is not None
    w.on_emitted(cand)
    assert w.propose() is None


def test_build_prompt_delegates_to_personality(
    config: RageDetectConfig,
) -> None:
    w = RageWedge(config=config)
    sig = RageSignal(app_name="twitter", pause_s=1.5)
    cand = EmissionCandidate(wedge_name="rage", payload=sig)
    personality = Mock()
    personality.build_rage_check_prompt.return_value = "you ok?"
    ctx = PromptContext(personality=personality, snapshot="")
    prompt = w.build_prompt(cand, ctx)
    assert prompt == "you ok?"
    personality.build_rage_check_prompt.assert_called_once_with("twitter")

from __future__ import annotations

from unittest.mock import Mock

from tokenpal.brain.intent import DriftSignal
from tokenpal.brain.wedge import EmissionCandidate, GatePolicy, PromptContext
from tokenpal.brain.wedges.drift import DriftWedge


def _intent_returning(sig: DriftSignal | None) -> Mock:
    intent = Mock()
    intent.check_drift.return_value = sig
    return intent


def test_propose_returns_none_when_no_drift() -> None:
    w = DriftWedge(intent=_intent_returning(None))
    assert w.propose() is None


def test_propose_returns_candidate_with_drift_payload() -> None:
    sig = DriftSignal(intent_text="finish PR", app_name="twitter", dwell_s=600)
    w = DriftWedge(intent=_intent_returning(sig))
    cand = w.propose()
    assert isinstance(cand, EmissionCandidate)
    assert cand.wedge_name == "drift"
    assert cand.payload is sig


def test_gate_is_needs_cap_open() -> None:
    assert DriftWedge.gate is GatePolicy.NEEDS_CAP_OPEN


def test_on_emitted_only_marks_on_success() -> None:
    intent = _intent_returning(None)
    w = DriftWedge(intent=intent)
    cand = EmissionCandidate(wedge_name="drift", payload=Mock())
    w.on_emitted(cand, success=False)
    intent.mark_drift_emitted.assert_not_called()
    w.on_emitted(cand, success=True)
    intent.mark_drift_emitted.assert_called_once()


def test_build_prompt_delegates_to_personality() -> None:
    sig = DriftSignal(intent_text="finish PR", app_name="twitter", dwell_s=600)
    w = DriftWedge(intent=_intent_returning(sig))
    cand = EmissionCandidate(wedge_name="drift", payload=sig)
    personality = Mock()
    personality.build_drift_nudge_prompt.return_value = "drifted"
    ctx = PromptContext(personality=personality, snapshot="")
    prompt = w.build_prompt(cand, ctx)
    assert prompt == "drifted"
    personality.build_drift_nudge_prompt.assert_called_once_with(
        intent_text="finish PR", app_name="twitter", dwell_s=600,
    )

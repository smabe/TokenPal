from __future__ import annotations

from unittest.mock import Mock

from tokenpal.brain.wedge import EmissionCandidate, GatePolicy, PromptContext
from tokenpal.brain.wedges.freeform import FreeformWedge


def test_propose_returns_none_when_not_eligible() -> None:
    w = FreeformWedge(is_eligible=lambda: False)
    assert w.propose() is None


def test_propose_returns_candidate_when_eligible() -> None:
    w = FreeformWedge(is_eligible=lambda: True)
    cand = w.propose()
    assert isinstance(cand, EmissionCandidate)
    assert cand.wedge_name == "freeform"
    assert cand.payload is None


def test_class_metadata_idle_fill_no_acknowledge() -> None:
    assert FreeformWedge.gate is GatePolicy.IDLE_FILL
    assert FreeformWedge.acknowledge_emit is False
    assert FreeformWedge.latency_budget == "freeform"


def test_build_prompt_delegates_to_personality() -> None:
    w = FreeformWedge(is_eligible=lambda: True)
    cand = EmissionCandidate(wedge_name="freeform", payload=None)
    personality = Mock()
    personality.build_freeform_prompt.return_value = "thought of the day"
    ctx = PromptContext(personality=personality, snapshot="")
    prompt = w.build_prompt(cand, ctx)
    assert prompt == "thought of the day"
    personality.build_freeform_prompt.assert_called_once_with()

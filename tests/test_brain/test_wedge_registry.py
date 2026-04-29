from __future__ import annotations

import pytest

from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    Wedge,
    WedgeRegistry,
)


class _DummyWedge(Wedge):
    name = "dummy"
    priority = 1
    gate = GatePolicy.IDLE_FILL

    def __init__(self, fire: bool = False) -> None:
        self.fire = fire

    def propose(self, now: float) -> EmissionCandidate | None:
        if not self.fire:
            return None
        return EmissionCandidate(wedge_name=self.name, payload="hi")


class _SecondDummy(_DummyWedge):
    name = "dummy2"


def test_propose_returns_none_or_candidate() -> None:
    silent = _DummyWedge(fire=False)
    firing = _DummyWedge(fire=True)
    assert silent.propose(now=0.0) is None
    cand = firing.propose(now=0.0)
    assert isinstance(cand, EmissionCandidate)
    assert cand.wedge_name == "dummy"
    assert cand.payload == "hi"


def test_registry_round_trip() -> None:
    reg = WedgeRegistry()
    assert len(reg) == 0
    a = _DummyWedge()
    b = _SecondDummy()
    reg.register(a)
    reg.register(b)
    assert len(reg) == 2
    assert list(reg) == [a, b]


def test_registry_rejects_duplicate_name() -> None:
    reg = WedgeRegistry()
    reg.register(_DummyWedge())
    with pytest.raises(ValueError, match="dummy"):
        reg.register(_DummyWedge())

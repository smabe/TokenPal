"""FreeformWedge — unprompted in-character thought when the cap is closed."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    LatencyBudget,
    PromptContext,
    Wedge,
)


class FreeformWedge(Wedge):
    name = "freeform"
    priority = 5
    gate = GatePolicy.IDLE_FILL
    latency_budget: ClassVar[LatencyBudget] = "freeform"
    acknowledge_emit: ClassVar[bool] = False

    def __init__(self, is_eligible: Callable[[], bool]) -> None:
        self._is_eligible = is_eligible

    def propose(self) -> EmissionCandidate | None:
        if not self._is_eligible():
            return None
        return EmissionCandidate(wedge_name=self.name, payload=None)

    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str:
        return ctx.personality.build_freeform_prompt()

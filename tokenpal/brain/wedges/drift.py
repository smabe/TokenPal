"""DriftWedge — wraps IntentStore.check_drift behind the Wedge interface."""

from __future__ import annotations

from typing import cast

from tokenpal.brain.intent import DriftSignal, IntentStore
from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    PromptContext,
    Wedge,
)


class DriftWedge(Wedge):
    name = "drift"
    priority = 50
    gate = GatePolicy.NEEDS_CAP_OPEN

    def __init__(self, intent: IntentStore) -> None:
        self._intent = intent

    def propose(self) -> EmissionCandidate | None:
        sig = self._intent.check_drift()
        if sig is None:
            return None
        return EmissionCandidate(wedge_name=self.name, payload=sig)

    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str:
        sig = cast(DriftSignal, candidate.payload)
        return ctx.personality.build_drift_nudge_prompt(
            intent_text=sig.intent_text,
            app_name=sig.app_name,
            dwell_s=sig.dwell_s,
        )

    def on_emitted(self, candidate: EmissionCandidate, success: bool) -> None:
        if success:
            self._intent.mark_drift_emitted()

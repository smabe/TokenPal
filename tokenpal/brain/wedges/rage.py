"""Rage wedge: wraps RageDetector and builds the rage-check prompt."""

from __future__ import annotations

from typing import cast

from tokenpal.brain.rage_detector import RageDetector, RageSignal
from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    PromptContext,
    Wedge,
)
from tokenpal.config.schema import RageDetectConfig
from tokenpal.senses.base import SenseReading


class RageWedge(Wedge):
    name = "rage"
    priority = 90
    gate = GatePolicy.BYPASS_CAP

    def __init__(self, config: RageDetectConfig | None = None) -> None:
        self._detector = RageDetector(config or RageDetectConfig())
        self._pending: RageSignal | None = None

    def ingest(self, readings: list[SenseReading]) -> None:
        signal = self._detector.ingest(readings)
        if signal is not None:
            self._pending = signal

    def propose(self) -> EmissionCandidate | None:
        if self._pending is None:
            return None
        return EmissionCandidate(wedge_name=self.name, payload=self._pending)

    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str:
        signal = cast(RageSignal, candidate.payload)
        return ctx.personality.build_rage_check_prompt(signal.app_name)

    def on_emitted(self, candidate: EmissionCandidate) -> None:
        self._pending = None
        self._detector.mark_emitted()

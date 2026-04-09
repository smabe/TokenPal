"""Builds the context window for the LLM from recent sense readings."""

from __future__ import annotations

import time
from collections import deque

from tokenpal.senses.base import SenseReading

# How long readings stay relevant (seconds)
_READING_TTL = 120.0

# Per-sense weights for interestingness scoring.
# Higher weight = this sense changing matters more for triggering comments.
_SENSE_WEIGHTS: dict[str, float] = {
    "app_awareness": 1.0,
    "idle": 1.0,
    "clipboard": 0.8,
    "screen_capture": 0.6,
    "hardware": 0.1,
    "time_awareness": 0.0,
}
_DEFAULT_WEIGHT = 0.5


class ContextWindowBuilder:
    """Maintains a rolling window of sense readings and builds LLM context."""

    def __init__(self, max_tokens: int = 2048) -> None:
        self._max_tokens = max_tokens
        self._readings: dict[str, SenseReading] = {}
        self._history: deque[SenseReading] = deque(maxlen=50)
        self._prev_summaries: dict[str, str] = {}

    def ingest(self, readings: list[SenseReading]) -> None:
        """Ingest a batch of new readings, keeping the latest per sense."""
        for r in readings:
            self._readings[r.sense_name] = r
            self._history.append(r)

    def snapshot(self) -> str:
        """Build a natural-language context string for the LLM prompt."""
        now = time.monotonic()
        lines: list[str] = []

        for sense_name, reading in sorted(self._readings.items()):
            age = now - reading.timestamp
            if age > _READING_TTL:
                continue
            # Use the summary directly — it's already human-readable
            lines.append(reading.summary)

        return "\n".join(lines)

    def interestingness(self) -> float:
        """Score how much the context has changed since the last acknowledged state.

        Read-only — does NOT consume the change. Call acknowledge() after
        a successful comment to mark the current state as seen.

        Uses per-sense weights so noisy senses (time, CPU jitter) don't
        inflate the score while meaningful events (app switches, idle
        returns) trigger comments immediately.
        """
        now = time.monotonic()
        score = 0.0

        for sense_name, reading in self._readings.items():
            if now - reading.timestamp > _READING_TTL:
                continue

            weight = _SENSE_WEIGHTS.get(sense_name, _DEFAULT_WEIGHT)
            prev = self._prev_summaries.get(sense_name)

            if prev is None:
                score += weight * reading.confidence
            elif reading.summary != prev:
                score += weight * reading.confidence

        return min(score, 1.0)

    def acknowledge(self) -> None:
        """Mark the current context as seen. Call after a successful comment."""
        for sense_name, reading in self._readings.items():
            self._prev_summaries[sense_name] = reading.summary

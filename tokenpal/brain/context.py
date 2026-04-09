"""Builds the context window for the LLM from recent sense readings."""

from __future__ import annotations

import time
from collections import deque

from tokenpal.senses.base import SenseReading

# How long readings stay relevant (seconds)
_READING_TTL = 120.0


class ContextWindowBuilder:
    """Maintains a rolling window of sense readings and builds LLM context."""

    def __init__(self, max_tokens: int = 2048) -> None:
        self._max_tokens = max_tokens
        self._readings: dict[str, SenseReading] = {}
        self._history: deque[SenseReading] = deque(maxlen=50)
        self._prev_snapshot: str = ""

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
        """Score how much the context has changed since the last snapshot.

        Returns 0.0 (identical) to 1.0 (completely different).
        Simple heuristic: ratio of changed lines.
        """
        current = self.snapshot()
        if not self._prev_snapshot:
            self._prev_snapshot = current
            return 1.0

        prev_lines = set(self._prev_snapshot.splitlines())
        curr_lines = set(current.splitlines())

        if not curr_lines:
            return 0.0

        changed = len(curr_lines - prev_lines)
        score = changed / max(len(curr_lines), 1)
        self._prev_snapshot = current
        return score

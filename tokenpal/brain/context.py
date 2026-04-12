"""Builds the context window for the LLM from recent sense readings."""

from __future__ import annotations

import time
from collections import deque

from tokenpal.senses.base import SenseReading

# Default TTL for readings when no per-sense TTL is registered (seconds)
_DEFAULT_READING_TTL = 120.0

# Per-sense weights for interestingness scoring.
# Higher weight = this sense changing matters more for triggering comments.
_SENSE_WEIGHTS: dict[str, float] = {
    "app_awareness": 0.3,
    "idle": 1.0,
    "clipboard": 0.8,
    "screen_capture": 0.6,
    "hardware": 0.3,
    "time_awareness": 0.15,
    "productivity": 0.1,
    "music": 0.2,
    "weather": 0.0,  # never triggers alone, enriches context only
}
_DEFAULT_WEIGHT = 0.5


class ContextWindowBuilder:
    """Maintains a rolling window of sense readings and builds LLM context."""

    def __init__(self, max_tokens: int = 2048) -> None:
        self._max_tokens = max_tokens
        self._readings: dict[str, SenseReading] = {}
        self._history: deque[SenseReading] = deque(maxlen=50)
        self._prev_summaries: dict[str, str] = {}
        self._sense_ttls: dict[str, float] = {}

    def register_ttl(self, sense_name: str, ttl_s: float) -> None:
        """Register a per-sense TTL (from AbstractSense.reading_ttl_s)."""
        self._sense_ttls[sense_name] = ttl_s

    def ttl_for(self, sense_name: str) -> float:
        """Return the TTL for a sense (or the default)."""
        return self._sense_ttls.get(sense_name, _DEFAULT_READING_TTL)

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
            if age > self.ttl_for(sense_name):
                continue
            # Include transition metadata when available
            if reading.changed_from:
                lines.append(f"{reading.summary} ({reading.changed_from})")
            else:
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
            if now - reading.timestamp > self.ttl_for(sense_name):
                continue

            weight = _SENSE_WEIGHTS.get(sense_name, _DEFAULT_WEIGHT)
            prev = self._prev_summaries.get(sense_name)

            if prev is None:
                score += weight * reading.confidence
            elif reading.summary != prev:
                score += weight * reading.confidence

        return min(score, 1.0)

    def activity_level(self) -> float:
        """Return 0.0–1.0 measuring how active the user is right now.

        Factors:
        - App switch frequency: how many distinct app_awareness changes in the
          last 60 seconds (more switching = more active).
        - Hardware load: high CPU/RAM suggests the machine is busy doing things.
        """
        now = time.monotonic()
        window = 60.0

        # --- App switch frequency ---
        # Count distinct app_awareness summary changes in the window
        app_summaries: list[str] = []
        for r in self._history:
            if r.sense_name == "app_awareness" and now - r.timestamp <= window:
                app_summaries.append(r.summary)

        # Count transitions (consecutive different summaries)
        switches = 0
        for i in range(1, len(app_summaries)):
            if app_summaries[i] != app_summaries[i - 1]:
                switches += 1

        # 5+ switches in 60s = max activity from app switching
        app_activity = min(switches / 5.0, 1.0)

        # --- Hardware load ---
        hw_reading = self._readings.get("hardware")
        hw_activity = 0.0
        if hw_reading and now - hw_reading.timestamp <= self.ttl_for("hardware"):
            cpu = hw_reading.data.get("cpu_percent", 0)
            ram = hw_reading.data.get("ram_percent", 0)
            # Use the higher of the two, scaled so 70%+ = meaningful activity
            hw_activity = min(max(cpu, ram) / 100.0, 1.0)

        # Blend: app switching is the stronger signal
        return min(app_activity * 0.7 + hw_activity * 0.3, 1.0)

    def active_readings(self) -> dict[str, SenseReading]:
        """Return non-expired readings keyed by sense name."""
        now = time.monotonic()
        return {
            name: r for name, r in self._readings.items()
            if now - r.timestamp <= self.ttl_for(name)
        }

    def prev_summary(self, sense_name: str) -> str | None:
        """Return the last acknowledged summary for a sense, or None."""
        return self._prev_summaries.get(sense_name)

    def acknowledge(self) -> None:
        """Mark the current context as seen. Call after a successful comment."""
        for sense_name, reading in self._readings.items():
            self._prev_summaries[sense_name] = reading.summary

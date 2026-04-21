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
    "hardware": 0.3,
    "time_awareness": 0.15,
    "productivity": 0.1,
    "music": 0.2,
    "weather": 0.0,  # never triggers alone, enriches context only
    "git": 0.8,      # commits and branch switches are high-signal events
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

    def clear_reading(self, sense_name: str) -> None:
        """Remove a sense's reading (e.g. when it returns None)."""
        self._readings.pop(sense_name, None)

    def snapshot(self) -> str:
        """Build a natural-language context string for the LLM prompt."""
        now = time.monotonic()

        # Composites first — some can suppress raw sense lines that they
        # supersede (e.g. AFK composite swallows the lone idle line).
        composite_entries = self._detect_composites()
        suppressed: set[str] = set()
        composite_lines: list[str] = []
        for line, suppress in composite_entries:
            composite_lines.append(line)
            suppressed.update(suppress)

        lines: list[str] = []
        for sense_name, reading in sorted(self._readings.items()):
            if sense_name in suppressed:
                continue
            age = now - reading.timestamp
            if age > self.ttl_for(sense_name):
                continue
            # Include transition metadata when available
            if reading.changed_from:
                lines.append(f"{reading.summary} ({reading.changed_from})")
            else:
                lines.append(reading.summary)

        lines.extend(composite_lines)
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

    def _detect_composites(self) -> list[tuple[str, set[str]]]:
        """Detect multi-signal patterns worth highlighting to the LLM.

        Returns (line, suppress_senses) tuples. Suppress_senses lists raw
        sense names whose summary the composite supersedes — snapshot()
        skips those to avoid printing the same fact twice.
        """
        composites: list[tuple[str, set[str]]] = []
        active = self.active_readings()

        hw = active.get("hardware")
        prod = active.get("productivity")
        music = active.get("music")
        time_r = active.get("time_awareness")
        idle = active.get("idle")
        app = active.get("app_awareness")
        typing = active.get("typing_cadence")

        # AFK: user is parked on the same app with no input. Highest-signal
        # composite — prepended so the 2-line cap can't squeeze it out.
        if (
            idle
            and idle.data.get("event") == "sustained"
            and app
            and self.prev_summary("app_awareness") == app.summary
            and (typing is None or typing.data.get("bucket") == "idle")
        ):
            idle_min = idle.data.get("idle_minutes", 0)
            composites.append((
                f"User is parked on \"{app.summary}\" — no input for "
                f"{int(idle_min)} minutes",
                {"idle", "productivity"},
            ))

        # High CPU + frequent app switching = something is grinding
        if hw and prod:
            cpu = hw.data.get("cpu_percent", 0)
            switches = prod.data.get("switches_per_hour", 0)
            if cpu > 70 and switches > 8:
                composites.append((
                    f"CPU is at {cpu}% and user has switched apps "
                    f"{int(switches)} times per hour",
                    set(),
                ))

        # Long focus + music = flow state
        if prod and music:
            focus_min = prod.data.get("time_in_current_min", 0)
            if focus_min > 30 and music.data.get("state") == "playing":
                composites.append((
                    f"User has been focused for {focus_min} minutes with music on",
                    set(),
                ))

        # Late night + long session
        if time_r and prod:
            hour = time_r.data.get("hour", 12)
            session_min = prod.data.get("session_minutes", 0)
            if hour >= 23 and session_min > 120:
                composites.append((
                    f"It's past 11 PM and user has been at it for "
                    f"{session_min // 60} hours",
                    set(),
                ))
            elif hour < 6 and hour >= 0 and session_min > 60:
                composites.append((
                    f"It's {hour} AM and user is still working "
                    f"after {session_min} minutes",
                    set(),
                ))

        return composites[:2]  # Cap at 2 to avoid bloating the context

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

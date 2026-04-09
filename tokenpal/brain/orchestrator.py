"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Callable

from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)

# Max comments in a rolling window (guardrail §2)
_MAX_COMMENTS_PER_WINDOW = 15
_WINDOW_SECONDS = 300.0


class Brain:
    """Polls senses, builds context, decides when to comment, generates via LLM."""

    def __init__(
        self,
        senses: list[AbstractSense],
        llm: AbstractLLMBackend,
        ui_callback: Callable[[str], None],
        personality: PersonalityEngine,
        status_callback: Callable[[str], None] | None = None,
        memory: MemoryStore | None = None,
        poll_interval_s: float = 2.0,
        comment_cooldown_s: float = 15.0,
        interestingness_threshold: float = 0.3,
        context_max_tokens: int = 2048,
        sense_intervals: dict[str, float] | None = None,
    ) -> None:
        self._senses = senses
        self._llm = llm
        self._ui_callback = ui_callback
        self._personality = personality
        self._status_callback = status_callback
        self._memory = memory
        self._last_recorded_app: str = ""
        self._poll_interval = poll_interval_s
        self._cooldown = comment_cooldown_s
        self._threshold = interestingness_threshold
        self._context = ContextWindowBuilder(max_tokens=context_max_tokens)
        self._last_comment_time: float = time.monotonic()
        self._running = False

        # Per-sense scheduling
        self._sense_intervals: dict[str, float] = sense_intervals or {}
        self._sense_last_polled: dict[str, float] = {}

        # LLM failure tracking
        self._consecutive_failures: int = 0
        self._last_confused_quip: float = 0.0

        # Silence tuning state
        self._consecutive_comments: int = 0
        self._comment_timestamps: list[float] = []

    async def start(self) -> None:
        """Initialize all components and start the main loop."""
        self._running = True

        for sense in self._senses:
            try:
                await sense.setup()
                log.info("Sense '%s' initialized", sense.sense_name)
            except Exception:
                log.exception("Failed to set up sense '%s'", sense.sense_name)
                sense.disable()

        await self._llm.setup()
        log.info("Brain started — polling every %.1fs", self._poll_interval)

        await self._run_loop()

    async def _run_loop(self) -> None:
        while self._running:
            try:
                readings = await self._poll_all_senses()
                if readings:
                    self._context.ingest(readings)

                snapshot = self._context.snapshot()
                log.debug("Context: %s", snapshot.replace("\n", " | "))

                # Update personality state each cycle
                self._personality.update_mood(snapshot)
                self._personality.update_gags(snapshot)
                self._record_memory_events(snapshot, readings)
                self._push_status()

                if self._should_comment():
                    await self._generate_comment(snapshot)

            except Exception:
                log.exception("Error in brain loop")

            await asyncio.sleep(self._poll_interval)

    async def _poll_all_senses(self) -> list[SenseReading]:
        now = time.monotonic()
        due: list[AbstractSense] = []

        for s in self._senses:
            if not s.enabled:
                continue
            last = self._sense_last_polled.get(s.sense_name, 0.0)
            interval = self._sense_intervals.get(s.sense_name, s.poll_interval_s)
            if now - last >= interval:
                due.append(s)

        if not due:
            return []

        tasks = [s.poll() for s in due]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        readings: list[SenseReading] = []
        for s, r in zip(due, results):
            self._sense_last_polled[s.sense_name] = now
            if isinstance(r, SenseReading):
                readings.append(r)
            elif isinstance(r, Exception):
                log.debug("Sense poll error: %s", r)
        return readings

    def _should_comment(self) -> bool:
        elapsed = time.monotonic() - self._last_comment_time
        if elapsed < self._cooldown:
            return False

        # Silence tuning: force a gap after 2 consecutive comments
        if self._consecutive_comments >= 2 and random.random() < 0.6:
            log.debug("Gate: forced silence after %d consecutive comments", self._consecutive_comments)
            self._consecutive_comments = 0
            return False

        # Random silence — flat 30% chance to stay quiet even when context is interesting
        if random.random() < 0.3:
            log.debug("Gate: random silence (30%%)")
            return False

        # Guardrail: cap at N comments per 5-minute window
        now = time.monotonic()
        self._comment_timestamps = [
            t for t in self._comment_timestamps if now - t < _WINDOW_SECONDS
        ]
        if len(self._comment_timestamps) >= _MAX_COMMENTS_PER_WINDOW:
            log.debug("Gate: rate limit — %d comments in window", len(self._comment_timestamps))
            return False

        # Time-of-day weighting: raise threshold at night for quieter behavior
        hour = datetime.now().hour
        threshold = self._threshold
        if 0 <= hour < 6:
            threshold = min(threshold + 0.2, 0.9)
        elif 22 <= hour <= 23:
            threshold = min(threshold + 0.1, 0.8)

        # Boredom bonus: gradually lower threshold after prolonged silence
        boredom_bonus = min(0.2, elapsed / 600.0)
        threshold = max(threshold - boredom_bonus, 0.05)

        score = self._context.interestingness()
        if score < threshold:
            log.debug("Gate: interestingness %.2f < threshold %.2f", score, threshold)
        return score >= threshold

    async def _generate_comment(self, snapshot: str | None = None) -> None:
        if snapshot is None:
            snapshot = self._context.snapshot()
        if not snapshot.strip():
            return

        # Guardrail: sensitive app detected — go silent
        if self._personality.check_sensitive_app(snapshot):
            log.debug("Sensitive app detected — staying silent")
            return

        # Check for easter eggs first — bypass LLM entirely
        egg = self._personality.check_easter_egg(snapshot)
        if egg:
            log.info("TokenPal (easter egg): %s", egg)
            self._personality.record_comment(egg)
            self._ui_callback(egg)
            self._context.acknowledge()
            self._last_comment_time = time.monotonic()
            self._consecutive_comments += 1
            self._comment_timestamps.append(time.monotonic())
            return

        memory_lines = self._memory.get_history_lines(10) if self._memory else None
        if memory_lines:
            log.debug("Memory: %s", " | ".join(memory_lines))
        prompt = self._personality.build_prompt(snapshot, memory_lines=memory_lines)

        try:
            response = await self._llm.generate(prompt)
            filtered = self._personality.filter_response(response.text)

            if filtered:
                log.info("TokenPal says: %s (%.0fms)", filtered, response.latency_ms)
                self._consecutive_failures = 0
                self._personality.record_comment(filtered)
                self._ui_callback(filtered)
                self._context.acknowledge()
                self._last_comment_time = time.monotonic()
                self._consecutive_comments += 1
                self._comment_timestamps.append(time.monotonic())
                # Record comment milestones
                if self._memory and self._personality._total_comments % 10 == 0:
                    self._memory.record_observation(
                        "system", "milestone",
                        f"Comment #{self._personality._total_comments}",
                    )
            else:
                log.debug("LLM chose silence")
                self._consecutive_comments = 0

        except Exception:
            self._consecutive_failures += 1
            log.warning("LLM generation failed (attempt %d)", self._consecutive_failures)

            # Serve a confused quip — but not too often (every 60s max)
            now = time.monotonic()
            if now - self._last_confused_quip >= 60.0:
                quip = self._personality.get_confused_quip()
                log.info("TokenPal (confused): %s", quip)
                self._ui_callback(quip)
                self._last_confused_quip = now
                self._last_comment_time = now

    def _record_memory_events(
        self, snapshot: str, readings: list[SenseReading]
    ) -> None:
        """Record meaningful events to persistent memory."""
        if not self._memory:
            return

        # App switch — only record when the foreground app changes
        current_app = self._personality._last_seen_app
        if current_app and current_app != self._last_recorded_app:
            self._memory.record_observation(
                "app_awareness", "app_switch", current_app
            )
            log.debug("Memory recorded: app_switch → %s", current_app)
            self._last_recorded_app = current_app

        # Idle return — check if any reading is from the idle sense
        for r in readings:
            if r.sense_name == "idle" and "returned" in r.summary.lower():
                self._memory.record_observation(
                    "idle", "idle_return", r.summary
                )

    def _push_status(self) -> None:
        """Push a status bar update to the UI."""
        if not self._status_callback:
            return

        mood = self._personality._mood.value
        active_senses = sum(1 for s in self._senses if s.enabled)
        elapsed = time.monotonic() - self._last_comment_time
        if elapsed < 60:
            ago = f"{int(elapsed)}s ago"
        else:
            ago = f"{int(elapsed / 60)}m ago"

        status = f"{mood} | {active_senses} senses | last spoke {ago} | Ctrl+C"
        self._status_callback(status)

    async def stop(self) -> None:
        """Shut down all components."""
        self._running = False
        for sense in self._senses:
            try:
                await sense.teardown()
            except Exception:
                log.exception("Error tearing down sense '%s'", sense.sense_name)
        await self._llm.teardown()
        log.info("Brain stopped")

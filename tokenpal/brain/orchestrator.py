"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Callable

from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)

# Max comments in a rolling window (guardrail §2)
_MAX_COMMENTS_PER_WINDOW = 8
_WINDOW_SECONDS = 300.0


class Brain:
    """Polls senses, builds context, decides when to comment, generates via LLM."""

    def __init__(
        self,
        senses: list[AbstractSense],
        llm: AbstractLLMBackend,
        ui_callback: Callable[[str], None],
        personality: PersonalityEngine,
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
        self._poll_interval = poll_interval_s
        self._cooldown = comment_cooldown_s
        self._threshold = interestingness_threshold
        self._context = ContextWindowBuilder(max_tokens=context_max_tokens)
        self._last_comment_time: float = time.monotonic()
        self._running = False

        # Per-sense scheduling
        self._sense_intervals: dict[str, float] = sense_intervals or {}
        self._sense_last_polled: dict[str, float] = {}

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

        # Silence tuning: after 3-4 consecutive comments, force a gap
        if self._consecutive_comments >= 3 and random.random() < 0.5:
            log.debug("Gate: forced silence after %d consecutive comments", self._consecutive_comments)
            self._consecutive_comments = 0
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

        prompt = self._personality.build_prompt(snapshot)

        try:
            response = await self._llm.generate(prompt)
            filtered = self._personality.filter_response(response.text)

            if filtered:
                log.info("TokenPal says: %s (%.0fms)", filtered, response.latency_ms)
                self._personality.record_comment(filtered)
                self._ui_callback(filtered)
                self._context.acknowledge()
                self._last_comment_time = time.monotonic()
                self._consecutive_comments += 1
                self._comment_timestamps.append(time.monotonic())
            else:
                log.debug("LLM chose silence")
                self._consecutive_comments = 0

        except Exception:
            log.exception("LLM generation failed")

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

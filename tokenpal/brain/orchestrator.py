"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)


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
    ) -> None:
        self._senses = senses
        self._llm = llm
        self._ui_callback = ui_callback
        self._personality = personality
        self._poll_interval = poll_interval_s
        self._cooldown = comment_cooldown_s
        self._threshold = interestingness_threshold
        self._context = ContextWindowBuilder(max_tokens=context_max_tokens)
        self._last_comment_time: float = 0.0
        self._running = False

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
                self._context.ingest(readings)

                if self._should_comment():
                    await self._generate_comment()

            except Exception:
                log.exception("Error in brain loop")

            await asyncio.sleep(self._poll_interval)

    async def _poll_all_senses(self) -> list[SenseReading]:
        tasks = [s.poll() for s in self._senses if s.enabled]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        readings: list[SenseReading] = []
        for r in results:
            if isinstance(r, SenseReading):
                readings.append(r)
            elif isinstance(r, Exception):
                log.debug("Sense poll error: %s", r)
        return readings

    def _should_comment(self) -> bool:
        elapsed = time.monotonic() - self._last_comment_time
        if elapsed < self._cooldown:
            return False
        return self._context.interestingness() >= self._threshold

    async def _generate_comment(self) -> None:
        snapshot = self._context.snapshot()
        if not snapshot.strip():
            return

        prompt = self._personality.build_prompt(snapshot)

        try:
            response = await self._llm.generate(prompt)
            filtered = self._personality.filter_response(response.text)

            if filtered:
                log.info("TokenPal says: %s (%.0fms)", filtered, response.latency_ms)
                self._ui_callback(filtered)
                self._last_comment_time = time.monotonic()
            else:
                log.debug("LLM chose silence")

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

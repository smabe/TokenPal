"""Periodic session summarizer — writes a terse handoff note every N seconds.

Every `interval_s` the summarizer asks the LLM to compress the last window of
observations into 2-3 sentences and writes the result to ``session_summaries``.
On restart, the orchestrator reads the most recent note via
``MemoryStore.get_latest_summary`` and injects it into the first observation
prompt so the buddy can reference last session's work.

Design notes (see plans/buddy-utility-wedges.md):
 * Skip-if-idle: if the window had no observations we don't burn an LLM call.
 * Privacy: the generated text passes through ``contains_sensitive_term``
   before INSERT; a match drops the whole row (no partial redaction).
 * Pacing: this path never emits bubbles, so it doesn't touch
   ``_should_comment``. It does share the same backend, so calls go through
   target-latency scaling to stay out of the brain loop's critical path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.llm.base import AbstractLLMBackend

log = logging.getLogger(__name__)


_SUMMARY_INSTRUCTION = """\
You are a terse summarizer writing a handoff note for yourself to read in the \
next session. Produce 2-3 plain-English sentences that capture what the user \
was doing: main app(s), any git activity, anything notable. No greetings, no \
personality, no speculation. Under 60 words. If the window is mostly idle or \
has nothing interesting, reply with the single word: NONE.

Window summary (last {window_minutes:.0f} minutes):
- Top apps: {apps}
- Signal counts: {sense_counts}
- Recent events:
{events}

Handoff note:"""


def _format_digest(digest: dict[str, Any], window_minutes: float) -> str:
    apps = (
        ", ".join(f"{name} ({count})" for name, count in digest.get("apps", []))
        or "none"
    )
    sense_counts = json.dumps(digest.get("sense_counts", {}), sort_keys=True)
    events = digest.get("events", [])
    if events:
        event_lines = "\n".join(
            f"  - {sense}/{event}: {summary}"
            for _ts, sense, event, summary in events
        )
    else:
        event_lines = "  (no events)"
    return _SUMMARY_INSTRUCTION.format(
        window_minutes=window_minutes,
        apps=apps,
        sense_counts=sense_counts,
        events=event_lines,
    )


class SessionSummarizer:
    """Background task that writes periodic session summaries to memory.db."""

    def __init__(
        self,
        memory: MemoryStore,
        llm: AbstractLLMBackend,
        interval_s: int = 300,
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._interval_s = max(60, int(interval_s))
        self._target_latency_s = target_latency_s
        self._min_tokens = min_tokens
        # Window start for the next summary. Initialized to "now" so the
        # first summary covers only activity from startup onwards (not
        # whatever historical observations happen to be in memory.db).
        self._window_start: float = time.time()
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        """Main loop — sleep interval_s, summarize, repeat."""
        log.info(
            "SessionSummarizer started (interval=%ds, target_latency=%s)",
            self._interval_s,
            self._target_latency_s,
        )
        try:
            while not self._stopped.is_set():
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(), timeout=self._interval_s
                    )
                    break  # stop was signalled
                except TimeoutError:
                    pass
                await self._tick()
        except asyncio.CancelledError:
            log.info("SessionSummarizer cancelled")
            raise
        except Exception:
            log.exception("SessionSummarizer crashed; stopping")

    def stop(self) -> None:
        self._stopped.set()

    async def _tick(self) -> None:
        """One summarization cycle. Always advances the window on success."""
        now = time.time()
        window_start = self._window_start
        window_end = now

        elapsed_min = (window_end - window_start) / 60
        if elapsed_min < 0.5:
            # Clock went backwards (sleep/resume); reset and wait again.
            self._window_start = window_end
            return

        obs_count = self._memory.count_observations_in_window(
            window_start, window_end
        )
        if obs_count == 0:
            log.debug(
                "Skip-if-idle: no observations in last %.1fm window", elapsed_min
            )
            self._window_start = window_end
            return

        digest = self._memory.get_window_digest(window_start, window_end)
        prompt = _format_digest(digest, elapsed_min)

        try:
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._target_latency_s,
                min_tokens=self._min_tokens,
            )
        except Exception:
            log.exception("SessionSummarizer LLM call failed; leaving window open")
            # Do NOT advance window — retry on next tick with the same range.
            return

        text = (response.text or "").strip()
        if not text or text.upper().startswith("NONE"):
            log.debug("Summarizer returned NONE; skipping write")
            self._window_start = window_end
            return

        if contains_sensitive_term(text):
            log.info("Summary dropped — contains sensitive term")
            self._window_start = window_end
            return

        self._memory.record_summary(text, window_start, window_end)
        log.info(
            "Session summary recorded (%.0fm window, %d obs, %d chars)",
            elapsed_min,
            obs_count,
            len(text),
        )
        self._window_start = window_end

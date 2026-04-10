"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Callable

from tokenpal.actions.base import AbstractAction
from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)

# Max comments in a rolling window (guardrail §2)
_MAX_COMMENTS_PER_WINDOW = 15
_WINDOW_SECONDS = 300.0


class Brain:
    """Polls senses, builds context, decides when to comment, generates via LLM."""

    # Max tool call rounds per comment to prevent infinite loops
    _MAX_TOOL_ROUNDS = 3

    def __init__(
        self,
        senses: list[AbstractSense],
        llm: AbstractLLMBackend,
        ui_callback: Callable[[str], None],
        personality: PersonalityEngine,
        status_callback: Callable[[str], None] | None = None,
        memory: MemoryStore | None = None,
        actions: list[AbstractAction] | None = None,
        poll_interval_s: float = 2.0,
        comment_cooldown_s: float = 15.0,
        interestingness_threshold: float = 0.3,
        context_max_tokens: int = 2048,
        sense_intervals: dict[str, float] | None = None,
    ) -> None:
        # User input queue (thread-safe, fed from main thread)
        self._user_input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
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

        # Actions (LLM-callable tools)
        self._actions: dict[str, AbstractAction] = {a.action_name: a for a in (actions or [])}
        self._tool_specs = [a.to_tool_spec() for a in self._actions.values()]

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
        self._loop = asyncio.get_running_loop()

        for sense in self._senses:
            try:
                await sense.setup()
                log.info("Sense '%s' initialized", sense.sense_name)
            except Exception:
                log.exception("Failed to set up sense '%s'", sense.sense_name)
                sense.disable()

        await self._llm.setup()
        log.info("Brain started — polling every %.1fs", self._poll_interval)

        # Say hello immediately so the buddy isn't silent on startup
        greeting = self._personality.get_startup_greeting()
        log.info("TokenPal (startup): %s", greeting)
        self._personality.record_comment(greeting)
        self._ui_callback(greeting)
        self._last_comment_time = time.monotonic()

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

                # Process any pending user input
                while not self._user_input_queue.empty():
                    try:
                        user_msg = self._user_input_queue.get_nowait()
                        await self._handle_user_input(user_msg)
                    except asyncio.QueueEmpty:
                        break

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

        # Activity level: user switching apps / hardware busy → talk more
        activity = self._context.activity_level()

        # Time-of-day weighting: raise threshold at night for quieter behavior
        hour = datetime.now().hour
        threshold = self._threshold
        if 0 <= hour < 6:
            threshold = min(threshold + 0.2, 0.9)
        elif 22 <= hour <= 23:
            threshold = min(threshold + 0.1, 0.8)

        # Activity bonus: high activity lowers the threshold (up to -0.15)
        threshold = max(threshold - activity * 0.15, 0.05)

        score = self._context.interestingness()

        # Boredom bonus: gradually lower threshold after prolonged silence,
        # but ONLY when there's at least *some* real context change (score > 0).
        # Without this guard, hardware jitter alone triggers empty comments.
        if score > 0:
            boredom_bonus = min(0.2, elapsed / 600.0)
            threshold = max(threshold - boredom_bonus, 0.1)

        log.debug(
            "Gate: interestingness %.2f vs threshold %.2f (activity %.2f)",
            score, threshold, activity,
        )
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
            # Use tool-calling path if actions are available
            if self._actions and self._tool_specs:
                response = await self._generate_with_tools(prompt)
            else:
                response = await self._llm.generate(prompt)

            if not response.text:
                log.debug("LLM returned empty content (model may need higher max_tokens)")
            filtered = self._personality.filter_response(response.text)

            if filtered:
                log.info("TokenPal says: %s (%.0fms)", filtered, response.latency_ms)
                # Re-enable tool specs if they were disabled due to failures
                if self._consecutive_failures > 0 and self._actions and not self._tool_specs:
                    self._tool_specs = [a.to_tool_spec() for a in self._actions.values()]
                    log.info("Re-enabled tool-calling after successful generation")
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
            if self._consecutive_failures <= 3:
                log.exception("LLM generation failed (attempt %d)", self._consecutive_failures)
            else:
                log.warning("LLM generation failed (attempt %d)", self._consecutive_failures)

            # If tool-calling keeps failing, disable it and fall back to plain generation
            if self._consecutive_failures == 3 and self._actions:
                log.warning("Disabling tool-calling — model may not support tools")
                self._tool_specs = []

            if self._consecutive_failures == 1:
                log.warning("Ollama appears to be down. Start it with: ollama serve")
                if self._status_callback:
                    self._status_callback("Ollama unreachable")

            # Serve a confused quip — but not too often (every 60s max)
            now = time.monotonic()
            if now - self._last_confused_quip >= 60.0:
                quip = self._personality.get_confused_quip()
                log.info("TokenPal (confused): %s", quip)
                self._ui_callback(quip)
                self._last_confused_quip = now
                self._last_comment_time = now

    async def _generate_with_tools(self, prompt: str) -> LLMResponse:
        """Multi-turn tool-calling loop. Sends prompt with tool defs, executes
        any tool calls the LLM requests, feeds results back, and repeats until
        the LLM produces a final text response (or we hit the round limit)."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        for _round in range(self._MAX_TOOL_ROUNDS):
            response = await self._llm.generate_with_tools(
                messages=messages,
                tools=self._tool_specs,
            )

            if not response.tool_calls:
                return response

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            results = await asyncio.gather(
                *(self._execute_tool_call(tc) for tc in response.tool_calls),
            )
            for tc, result_text in zip(response.tool_calls, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        log.warning("Hit tool round limit (%d), forcing text response", self._MAX_TOOL_ROUNDS)
        return await self._llm.generate_with_tools(messages=messages, tools=[])

    async def _execute_tool_call(self, tc: ToolCall) -> str:
        """Execute a single tool call and return the result text."""
        action = self._actions.get(tc.name)
        if action is None:
            log.warning("LLM called unknown action: %s", tc.name)
            return f"Unknown tool '{tc.name}'."
        try:
            result = await action.execute(**tc.arguments)
            log.info("Action '%s' executed: %s", tc.name, result.output)
            return result.output
        except Exception as e:
            log.warning("Action '%s' failed: %s", tc.name, e)
            return f"Error: {e}"

    def submit_user_input(self, text: str) -> None:
        """Thread-safe: enqueue user text from the main thread."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._user_input_queue.put_nowait, text
            )

    async def _handle_user_input(self, user_message: str) -> None:
        """Respond to direct user input with a conversational prompt."""
        snapshot = self._context.snapshot()
        prompt = self._personality.build_conversation_prompt(
            user_message, snapshot
        )

        try:
            response = await self._llm.generate(prompt, max_tokens=1024)
            log.debug("Raw conversation response: %r", response.text[:200])
            filtered = self._personality.filter_conversation_response(
                response.text
            )
            if filtered:
                log.info("TokenPal (reply): %s", filtered)
                self._personality.record_comment(filtered)
                self._ui_callback(filtered)
                self._last_comment_time = time.monotonic()
        except Exception:
            log.exception("Failed to generate conversation response")
            quip = self._personality.get_confused_quip()
            self._ui_callback(quip)

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

        mood = self._personality.mood
        model = self._llm.model_name
        voice = self._personality.voice_name
        elapsed = time.monotonic() - self._last_comment_time
        if elapsed < 60:
            ago = f"{int(elapsed)}s ago"
        else:
            ago = f"{int(elapsed / 60)}m ago"

        parts = [model]
        if voice:
            parts.append(voice)
        parts.append(mood)
        parts.append(f"spoke {ago}")
        status = " | ".join(parts)
        self._status_callback(status)

    async def stop(self) -> None:
        """Shut down all components."""
        self._running = False
        for sense in self._senses:
            try:
                await sense.teardown()
            except Exception:
                log.exception("Error tearing down sense '%s'", sense.sense_name)
        for action in self._actions.values():
            try:
                await action.teardown()
            except Exception:
                log.exception("Error tearing down action '%s'", action.action_name)
        await self._llm.teardown()
        log.info("Brain stopped")

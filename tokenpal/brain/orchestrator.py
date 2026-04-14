"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from tokenpal.actions.base import AbstractAction
from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import SENSITIVE_APPS, PersonalityEngine
from tokenpal.config.schema import ConversationConfig
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversation session — tracks multi-turn history for user conversations
# ---------------------------------------------------------------------------

@dataclass
class ConversationSession:
    """Tracks state for an active multi-turn conversation."""

    history: list[dict[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    max_turns: int = 10
    timeout_s: float = 120.0

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_activity) > self.timeout_s

    @property
    def is_active(self) -> bool:
        return len(self.history) > 0 and not self.is_expired

    @property
    def turn_count(self) -> int:
        """Number of completed turn pairs (user + assistant)."""
        return sum(1 for m in self.history if m["role"] == "assistant")

    def add_user_turn(self, content: str) -> None:
        self.last_activity = time.monotonic()
        self.history.append({"role": "user", "content": content})
        self._enforce_cap()

    def add_assistant_turn(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})
        self._enforce_cap()

    def _enforce_cap(self) -> None:
        """Drop oldest turn pair when over budget."""
        if len(self.history) > self.max_turns * 2:
            del self.history[:2]


# Max comments in a rolling window (guardrail §2)
_MAX_COMMENTS_PER_WINDOW = 8
_WINDOW_SECONDS = 300.0

# Forced silence after N consecutive comments (seconds)
_FORCED_SILENCE_AFTER = 3
_FORCED_SILENCE_DURATION = 120.0

# Freeform chance for voices with 50+ example lines
_FREEFORM_CHANCE_RICH = 0.20

# Topic focus hints prepended to the context snapshot
_TOPIC_FOCUS_HINTS: dict[str, str] = {
    "weather": "Focus your comment on the weather conditions.",
    "music": "Focus your comment on what they're listening to.",
    "productivity": "Comment on their work pattern or focus level.",
    "hardware": "Comment on the machine's state (CPU, RAM, etc).",
    "app_awareness": "Comment on what they're doing right now.",
    "time_awareness": "Comment on the time of day or how long they've been working.",
    "idle": "Comment on them returning from being away.",
}


class Brain:
    """Polls senses, builds context, decides when to comment, generates via LLM."""

    # Max tool call rounds per comment to prevent infinite loops
    _MAX_TOOL_ROUNDS = 3
    # Freeform (unprompted) thought settings
    _FREEFORM_MIN_GAP_S = 90.0
    _FREEFORM_CHANCE = 0.15

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
        comment_cooldown_s: float = 30.0,
        interestingness_threshold: float = 0.4,
        context_max_tokens: int = 2048,
        sense_intervals: dict[str, float] | None = None,
        conversation: ConversationConfig | None = None,
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

        # Pause flag — suppresses comments (e.g. during voice training)
        self._paused = False

        # Silence tuning state
        self._consecutive_comments: int = 0
        self._comment_timestamps: list[float] = []
        self._forced_silence_until: float = 0.0

        # Topic roulette state
        self._recent_topics: deque[str] = deque(maxlen=10)

        # Conversation session state
        self._conversation: ConversationSession | None = None
        self._conv_config = conversation or ConversationConfig()

    async def start(self) -> None:
        """Initialize all components and start the main loop."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        for sense in self._senses:
            try:
                await sense.setup()
                self._context.register_ttl(sense.sense_name, sense.reading_ttl_s)
                log.info("Sense '%s' initialized", sense.sense_name)
            except Exception:
                log.exception("Failed to set up sense '%s'", sense.sense_name)
                sense.disable()

        if self._status_callback:
            self._status_callback(f"Loading {self._llm.model_name}...")
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

                # Clean up expired conversation sessions
                if self._conversation and self._conversation.is_expired:
                    log.debug(
                        "Conversation session expired (%.0fs idle, %d turns)",
                        time.monotonic() - self._conversation.last_activity,
                        self._conversation.turn_count,
                    )
                    self._clear_conversation()

                # High-signal events bypass the normal gate
                has_urgent = any(
                    r.sense_name == "git" and r.changed_from
                    for r in readings
                )
                if has_urgent or self._should_comment():
                    await self._generate_comment(snapshot)
                elif self._should_freeform():
                    await self._generate_freeform_comment()

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
            # r is None: transition-only senses return None between events;
            # reading_ttl_s already bounds how long the cached reading stays
            # active, so don't clear here (would wipe the reading before the
            # gate's cooldown clears).
        return readings

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = value

    @property
    def _in_conversation(self) -> bool:
        """True when an active conversation session is suppressing observations."""
        return self._conversation is not None and self._conversation.is_active

    def reset_conversation(self) -> None:
        """Clear the conversation session. Thread-safe: can be called from main thread."""
        if self._loop is None:
            self._clear_conversation()
            return
        try:
            self._loop.call_soon_threadsafe(self._clear_conversation)
        except RuntimeError:
            self._clear_conversation()

    def _clear_conversation(self) -> None:
        """Drop references to message contents before clearing the buffer.
        (Python strings are immutable — this releases refs, not secure zeroing.)"""
        if self._conversation is not None:
            log.debug("Conversation session cleared (%d turns)", self._conversation.turn_count)
            for msg in self._conversation.history:
                if "content" in msg:
                    msg["content"] = ""
            self._conversation.history.clear()
        self._conversation = None

    def _should_comment(self) -> bool:
        if self._paused:
            return False
        if self._in_conversation:
            return False

        now = time.monotonic()
        elapsed = now - self._last_comment_time

        # Forced silence period after consecutive comment burst
        if now < self._forced_silence_until:
            return False

        # Dynamic cooldown: high activity = 30s, idle = 90s
        activity = self._context.activity_level()
        dynamic_cooldown = max(self._cooldown, 90.0 - activity * 60.0)
        # Add jitter so comments don't feel like a metronome
        jittered_cooldown = dynamic_cooldown + random.uniform(0, 15.0)
        if elapsed < jittered_cooldown:
            return False

        # Long gap between comments breaks a burst — reset before the check below.
        if elapsed > _FORCED_SILENCE_DURATION and self._consecutive_comments > 0:
            self._consecutive_comments = 0

        # Hard silence after N consecutive comments — force a 2-minute breather
        if self._consecutive_comments >= _FORCED_SILENCE_AFTER:
            log.debug(
                "Gate: forced %ds silence after %d consecutive comments",
                int(_FORCED_SILENCE_DURATION), self._consecutive_comments,
            )
            self._forced_silence_until = now + _FORCED_SILENCE_DURATION
            self._consecutive_comments = 0
            return False

        # Guardrail: cap at N comments per 5-minute window
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

        # Activity bonus: high activity lowers the threshold (up to -0.15)
        threshold = max(threshold - activity * 0.15, 0.05)

        score = self._context.interestingness()

        # Boredom bonus: gradually lower threshold after prolonged silence,
        # but ONLY when there's at least *some* real context change (score > 0).
        if score > 0:
            boredom_bonus = min(0.2, elapsed / 600.0)
            threshold = max(threshold - boredom_bonus, 0.1)

        log.debug(
            "Gate: interestingness %.2f vs threshold %.2f (activity %.2f)",
            score, threshold, activity,
        )
        return score >= threshold

    def _should_freeform(self) -> bool:
        """Check if we should generate an unprompted in-character thought."""
        if self._paused:
            return False
        if self._in_conversation:
            return False
        if not self._personality.has_rich_voice:
            return False

        now = time.monotonic()

        # Respect forced silence — same gate as _should_comment()
        if now < self._forced_silence_until:
            return False

        elapsed = now - self._last_comment_time
        if elapsed < self._FREEFORM_MIN_GAP_S:
            return False

        # Reuse already-pruned timestamps from _should_comment()
        if len(self._comment_timestamps) >= _MAX_COMMENTS_PER_WINDOW:
            return False

        chance = _FREEFORM_CHANCE_RICH if self._personality.has_rich_voice else self._FREEFORM_CHANCE
        if random.random() >= chance:
            return False

        log.debug("Gate: freeform thought triggered (%.0fs since last)", elapsed)
        return True

    def _emit_comment(self, text: str, acknowledge: bool = False) -> None:
        """Record a comment and show it to the user."""
        self._personality.record_comment(text)
        self._ui_callback(text)
        if acknowledge:
            self._context.acknowledge()
        self._last_comment_time = time.monotonic()
        self._consecutive_comments += 1
        self._comment_timestamps.append(time.monotonic())

    async def _generate_freeform_comment(self) -> None:
        """Generate an unprompted in-character thought."""
        prompt = self._personality.build_freeform_prompt()

        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug("Generating freeform comment...")
            response = await self._llm.generate(prompt)
            self._push_status()

            filtered = self._personality.filter_response(response.text)
            if filtered:
                log.info("TokenPal (freeform): %s (%.0fms)", filtered, response.latency_ms)
                self._emit_comment(filtered)
            else:
                log.debug("Freeform filtered out: %r", response.text[:80])

        except Exception:
            log.exception("Freeform generation failed")
            self._push_status()

    def _pick_topic(self) -> str:
        """Weighted random topic selection, penalizing recently used topics."""
        now = time.monotonic()
        available: dict[str, float] = {}
        active = self._context.active_readings()

        for sense_name, reading in active.items():
            # Freshness: newer readings are more interesting
            ttl = self._context.ttl_for(sense_name)
            age = now - reading.timestamp
            freshness = max(0.1, 1.0 - age / ttl)
            # Novelty penalty: recently commented topics are less interesting
            recent_count = sum(1 for t in self._recent_topics if t == sense_name)
            novelty = max(0.1, 1.0 - recent_count * 0.3)
            # Change bonus: readings that just changed are more comment-worthy
            prev = self._context.prev_summary(sense_name)
            change_bonus = 1.5 if (prev is None or reading.summary != prev) else 0.5

            available[sense_name] = freshness * novelty * change_bonus

        if not available:
            return "app_awareness"

        # Hard block: no 3+ consecutive same-topic comments
        if len(self._recent_topics) >= 2:
            t1, t2 = self._recent_topics[-2], self._recent_topics[-1]
            if t1 == t2 and t1 in available:
                blocked = t1
                available[blocked] = 0.0
                if not any(v > 0 for v in available.values()):
                    for k in available:
                        if k != blocked:
                            available[k] = 1.0

        names = list(available.keys())
        weights = [available[n] for n in names]
        total = sum(weights)
        if total == 0:
            return names[0] if names else "app_awareness"

        return random.choices(names, weights=weights, k=1)[0]

    def _apply_topic_focus(self, snapshot: str, topic: str) -> str:
        """Prepend a focus hint so the LLM knows what aspect to comment on."""
        hint = _TOPIC_FOCUS_HINTS.get(topic)
        if hint:
            return f"[{hint}]\n\n{snapshot}"
        return snapshot

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
            self._emit_comment(egg, acknowledge=True)
            return

        # Topic roulette: pick what to focus on and hint the LLM
        topic = self._pick_topic()
        snapshot = self._apply_topic_focus(snapshot, topic)

        memory_lines = self._memory.get_history_lines(10) if self._memory else None
        callback_lines = (
            self._memory.get_pattern_callbacks(sensitive_apps=SENSITIVE_APPS)
            if self._memory
            else None
        )
        if memory_lines:
            log.debug("Memory: %s", " | ".join(memory_lines))
        prompt = self._personality.build_prompt(
            snapshot, memory_lines=memory_lines, callback_lines=callback_lines,
        )

        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug("Generating observation comment...")
            # Use tool-calling path if actions are available
            if self._actions and self._tool_specs:
                response = await self._generate_with_tools(prompt)
            else:
                response = await self._llm.generate(prompt)
            self._push_status()

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
                self._emit_comment(filtered, acknowledge=True)
                self._recent_topics.append(topic)
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

    async def _generate_with_tools(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Multi-turn tool-calling loop. Sends prompt (or pre-built messages)
        with tool defs, executes tool calls, feeds results back, and repeats
        until the LLM produces a final text response (or we hit the round limit)."""
        if messages is None:
            assert prompt is not None
            messages = [{"role": "user", "content": prompt}]

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
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(
                self._user_input_queue.put_nowait, text
            )
        except RuntimeError:
            log.warning("Brain event loop closed — input dropped")

    async def _handle_user_input(self, user_message: str) -> None:
        """Respond to direct user input using multi-turn conversation context."""
        # PRIVACY: check sensitive apps BEFORE building prompt or touching history
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            log.debug("Sensitive app detected during conversation — clearing session")
            self._clear_conversation()
            self._ui_callback("I'll look away while you handle that.")
            return

        # Start or continue conversation session
        if self._conversation is None or self._conversation.is_expired:
            self._conversation = ConversationSession(
                max_turns=self._conv_config.max_turns,
                timeout_s=self._conv_config.timeout_s,
            )
            log.debug("New conversation session started")

        # Record user turn (also resets timeout)
        self._conversation.add_user_turn(user_message)

        # Build messages array: [system, history..., context, user]
        system_msg = self._personality.build_conversation_system_message()
        context_msg = self._personality.build_context_injection(snapshot)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_msg},
            # history[:-1]: the user turn was just appended by add_user_turn(),
            # so we exclude it here and re-add it below with fresh context injected
            *self._conversation.history[:-1],
            {"role": "system", "content": context_msg},  # fresh context
            {"role": "user", "content": user_message},    # current turn
        ]

        try:
            if self._status_callback:
                self._status_callback("replying...")
            log.debug(
                "Conversation turn %d (%.30s...)",
                self._conversation.turn_count + 1,
                user_message,
            )
            if self._actions and self._tool_specs:
                response = await self._generate_with_tools(messages=messages)
            else:
                response = await self._llm.generate_with_tools(
                    messages=messages,
                    tools=[],
                    max_tokens=self._conv_config.max_response_tokens,
                )
            self._push_status()

            filtered = self._personality.filter_conversation_response(
                response.text
            )
            if filtered:
                self._conversation.add_assistant_turn(filtered)
                log.info("TokenPal (reply): %s", filtered)
                self._personality.record_comment(filtered)
                self._ui_callback(filtered)
                self._last_comment_time = time.monotonic()
            else:
                # Record placeholder so history stays coherent
                self._conversation.add_assistant_turn("[no response]")
                log.debug("Conversation response filtered: %r", response.text[:80])
                quip = self._personality.get_confused_quip()
                self._ui_callback(quip)
        except Exception:
            log.exception("Failed to generate conversation response")
            # Don't record failed exchange — remove the user turn we just added
            if self._conversation.history and self._conversation.history[-1]["role"] == "user":
                self._conversation.history.pop()
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
        elapsed = time.monotonic() - self._last_comment_time
        if elapsed < 60:
            ago = f"{int(elapsed)}s ago"
        else:
            ago = f"{int(elapsed / 60)}m ago"

        # Server indicator: show where inference is running
        server_label = ""
        api = self._llm.api_url
        hostname = urlparse(api).hostname or ""
        if self._llm.using_fallback:
            primary_host = urlparse(self._llm.primary_url).hostname or ""
            server_label = f"{primary_host} (fallback)"
        elif hostname not in ("localhost", "127.0.0.1", "::1", ""):
            server_label = hostname

        # Pull live sense data for the status bar
        active = self._context.active_readings()

        app_label = ""
        if "app_awareness" in active:
            app_label = active["app_awareness"].summary[:12]

        weather_label = ""
        if "weather" in active:
            weather_label = self._abbreviate_weather(active["weather"].summary)

        music_label = ""
        if "music" in active:
            music_label = active["music"].summary[:25]

        voice = self._personality.voice_name

        parts = [mood]
        if server_label:
            parts.append(server_label)
        parts.append(self._llm.model_name)
        if voice:
            parts.append(voice)
        if app_label:
            parts.append(app_label)
        if weather_label:
            parts.append(weather_label)
        if music_label:
            parts.append(music_label)
        parts.append(f"spoke {ago}")
        status = " | ".join(parts)
        self._status_callback(status)

    @staticmethod
    def _abbreviate_weather(summary: str) -> str:
        """Condense weather summary to 'temp condition' for the status bar."""
        # Summary format: "It's 73°F and overcast outside"
        m = re.search(r"(\d+).?([FC]).*?and\s+(.+?)\s+outside", summary)
        if m:
            return f"{m.group(1)}{m.group(2)} {m.group(3)}"
        return summary[:15]

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

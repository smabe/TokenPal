"""The Brain — central orchestrator that polls senses, feeds the LLM, and decides when to comment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.brain.agent import AgentRunner, AgentSession, fmt_args
from tokenpal.brain.app_enricher import AppEnricher
from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.eod_summary import EODSummary, today_str, yesterday_str
from tokenpal.brain.git_nudge import GitNudgeDetector, GitNudgeSignal
from tokenpal.brain.idle_tools import IdleFireResult, IdleToolRoller, build_context
from tokenpal.brain.intent import DriftSignal, IntentStore
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.rage_detector import RageDetector, RageSignal
from tokenpal.brain.personality import SENSITIVE_APPS, PersonalityEngine
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.research import ResearchRunner, ResearchSession, Source
from tokenpal.brain.session_summarizer import SessionSummarizer
from tokenpal.brain.stop_reason import AgentStopReason, ResearchStopReason
from tokenpal.config.consent import Category, has_consent
from tokenpal.config.schema import (
    AgentConfig,
    ConversationConfig,
    GitNudgeConfig,
    IdleToolsConfig,
    IntentConfig,
    MinTokensPerPathConfig,
    RageDetectConfig,
    ResearchConfig,
    SessionSummaryConfig,
    TargetLatencyConfig,
)
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall
from tokenpal.senses.base import AbstractSense, SenseReading

log = logging.getLogger(__name__)


_SENTENCE_ENDERS = (".", "!", "?", "…")
# Whitespace plus closing quotes/brackets/markdown that may trail a sentence.
_SENTENCE_TRAILERS = " \t\n\r\"')]}*`"


def _ends_with_sentence(text: str) -> bool:
    """True when `text` ends on terminal sentence punctuation, ignoring any
    trailing whitespace or closing quote/bracket characters."""
    stripped = text.rstrip(_SENTENCE_TRAILERS)
    return bool(stripped) and stripped.endswith(_SENTENCE_ENDERS)


def _trim_to_last_sentence(text: str) -> str:
    """Return the longest prefix of `text` that ends on sentence-terminal
    punctuation. Empty string if none found."""
    best = max(text.rfind(ch) for ch in _SENTENCE_ENDERS)
    return text[: best + 1] if best >= 0 else ""


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

# Forced silence after N consecutive SUPPRESSED outputs. Stops the brain
# loop from burning LLM calls every tick when the model is stuck in a
# template lock-in (near-duplicate or prefix-lock variants of the same
# line). Shorter than the 3-consecutive-comment forced silence because
# suppressions are usually a symptom of a drift the LLM can't escape
# without the prompt being reshuffled by a real sense change.
_FORCED_SILENCE_AFTER_SUPPRESSIONS = 5

# Freeform chance for voices with 50+ example lines
_FREEFORM_CHANCE_RICH = 0.20

# Near-duplicate guard — reject if char-trigram Jaccard vs. any recent
# output meets or exceeds this threshold.
_NEAR_DUPLICATE_JACCARD = 0.70

# Anchor-phrase lock guard. A candidate is suppressed when its leading
# N tokens match the leading N tokens of at least M recent outputs. Catches
# structural template lock-in ("Jake, good cop... X got more Y than Z") that
# surface-Jaccard misses because the tail varies every time.
_PREFIX_LOCK_TOKEN_COUNT = 3
_PREFIX_LOCK_MIN_MATCHES = 3
_RECENT_OUTPUTS_MAX = 10

class BrainMode(StrEnum):
    """Heavyweight mode of the brain. Conversation isn't a mode because it
    carries history state on ``_conversation`` rather than a flag."""

    IDLE = "idle"
    AGENT = "agent"
    RESEARCH = "research"


@dataclass
class AgentBridge:
    """Config + host callbacks needed to run /agent."""

    config: AgentConfig
    log_callback: Callable[[str], None] | None = None
    confirm_callback: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None


@dataclass
class ResearchBridge:
    """Config + host callbacks needed to run /research."""

    config: ResearchConfig
    log_callback: Callable[[str], None] | None = None


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

    # Max tool call rounds per comment to prevent infinite loops.
    # Matches the agent-mode step cap so research + a couple of follow-up
    # search_web/fetch_url calls all fit in one conversation turn.
    _MAX_TOOL_ROUNDS = 8
    # Max follow-up calls to finish a conversation reply that hit max_tokens
    _MAX_CONTINUATIONS = 2
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
        agent_bridge: AgentBridge | None = None,
        research_bridge: ResearchBridge | None = None,
        log_callback: Callable[..., None] | None = None,
        idle_tools_config: IdleToolsConfig | None = None,
        target_latency_s: TargetLatencyConfig | None = None,
        min_tokens_per_path: MinTokensPerPathConfig | None = None,
        session_summary_config: SessionSummaryConfig | None = None,
        intent_config: IntentConfig | None = None,
        rage_detect_config: RageDetectConfig | None = None,
        git_nudge_config: GitNudgeConfig | None = None,
    ) -> None:
        # User input queue (thread-safe, fed from main thread)
        self._user_input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._agent_goal_queue: asyncio.Queue[str] = asyncio.Queue()
        self._research_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._senses = senses
        self._llm = llm
        self._ui_callback = ui_callback
        self._log_callback = log_callback
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
        self._suppressed_streak: int = 0
        self._comment_timestamps: list[float] = []
        self._forced_silence_until: float = 0.0

        # Topic roulette state
        self._recent_topics: deque[str] = deque(maxlen=10)

        # Near-duplicate guard — drops observation/freeform lines that rhyme
        # too closely with recent output (prevents prompt-cache template lock-in).
        self._recent_outputs: deque[str] = deque(maxlen=_RECENT_OUTPUTS_MAX)

        # Conversation session state
        self._conversation: ConversationSession | None = None
        self._conv_config = conversation or ConversationConfig()

        self._agent = agent_bridge or AgentBridge(config=AgentConfig())
        self._research = research_bridge or ResearchBridge(config=ResearchConfig())
        self._mode: BrainMode = BrainMode.IDLE

        # Proactive scheduler (phase 3). Shared across all focus actions.
        # Pauses during active conversation and sensitive-app detection.
        self._proactive = ProactiveScheduler(
            ui_callback=self._ui_callback,
            is_paused=self._proactive_paused,
        )

        # Inject brain-scoped dependencies (scheduler, memory, ui_callback)
        # into any action that advertises a matching attribute. Phase 3 focus
        # actions opt in via underscore-prefixed attrs set during __init__.
        self._inject_brain_deps()

        # Idle-tool roller (third emission path — fires only when the comment
        # gate chose silence, so it fills dead air without inflating rate).
        self._idle_tools_config = idle_tools_config or IdleToolsConfig()
        self._idle_tools = IdleToolRoller(
            config=self._idle_tools_config,
            actions=self._actions,
        )
        # Session-scoped: computed once at startup from memory.db.
        self._first_session_of_day: bool = True
        self._session_started_at: float = time.monotonic()

        self._app_enricher = (
            AppEnricher(memory=self._memory) if self._memory is not None else None
        )

        # Target-latency budgets + token floors per call-path.
        # See plans/shipped/gpu-scaling.md.
        self._budgets: TargetLatencyConfig = target_latency_s or TargetLatencyConfig()
        self._min_tokens: MinTokensPerPathConfig = (
            min_tokens_per_path or MinTokensPerPathConfig()
        )

        # Session handoff — periodic summarizer + the last note we loaded
        # at startup. See plans/buddy-utility-wedges.md.
        self._session_summary_config: SessionSummaryConfig = (
            session_summary_config or SessionSummaryConfig()
        )
        self._previous_session_note: str | None = None
        self._session_summarizer: SessionSummarizer | None = None
        self._session_summary_task: asyncio.Task[None] | None = None

        # Intent tracking — only when memory is available.
        self._intent_config: IntentConfig = intent_config or IntentConfig()
        self._intent: IntentStore | None = (
            IntentStore(memory=self._memory, config=self._intent_config)
            if self._memory is not None and self._memory.enabled
            else None
        )
        self._last_app_for_intent: str = ""

        # End-of-day summary — fires once per local date on first boot of
        # the day (async post-startup) and on /summary command.
        self._eod: EODSummary | None = (
            EODSummary(
                memory=self._memory,
                llm=self._llm,
                personality=self._personality,
                target_latency_s=self._budgets.observation,
                min_tokens=self._min_tokens.observation,
            )
            if self._memory is not None and self._memory.enabled
            else None
        )

        # Rage / frustration detector (opt-in). Consumes typing_cadence +
        # app_awareness readings only.
        self._rage_config: RageDetectConfig = rage_detect_config or RageDetectConfig()
        self._rage: RageDetector = RageDetector(config=self._rage_config)

        # Proactive WIP-commit nudge.
        self._git_nudge_config: GitNudgeConfig = git_nudge_config or GitNudgeConfig()
        self._git_nudge: GitNudgeDetector = GitNudgeDetector(
            config=self._git_nudge_config
        )
        self._user_present: bool = False

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

        # Resolve first-session-of-day once; warm evergreen tool cache in
        # the background so the hot path never blocks on an HTTP call.
        self._first_session_of_day = self._compute_first_session_of_day()
        if self._idle_tools_config.enabled:
            asyncio.create_task(self._idle_tools.warm_daily_cache())

        # Session handoff: read last summary (if any) + spawn the periodic
        # summarizer task. Summaries never emit bubbles; they just write to
        # memory.db so the next session can reference them.
        self._load_previous_session_note()
        self._start_session_summarizer()

        # EOD: if yesterday has unfrozen activity and we haven't shown the
        # bubble yet, fire it off-thread. Startup should not block on it.
        self._maybe_fire_pending_eod()

        # Git nudge: hydrate initial state in the background so a WIP
        # branch that was already stale at launch can nudge without
        # waiting for the next git change.
        if self._git_nudge.enabled:
            asyncio.create_task(self._git_nudge.hydrate())

        await self._run_loop()

    def _maybe_fire_pending_eod(self) -> None:
        """Spawn the EOD bubble for yesterday if it hasn't been shown yet."""
        if self._eod is None or self._memory is None:
            return
        date_str = yesterday_str()
        if self._memory.has_shown_eod(date_str):
            return
        asyncio.create_task(self._emit_eod_bubble(date_str, mark_shown=True))

    async def _emit_eod_bubble(
        self, date_str: str, *, mark_shown: bool
    ) -> bool:
        """Render and emit an EOD bubble for date_str. Returns True on emit."""
        if self._eod is None or self._memory is None:
            return False
        line = await self._eod.generate(date_str)
        if not line:
            log.debug("EOD: nothing to say for %s", date_str)
            return False
        log.info("TokenPal (EOD %s): %s", date_str, line)
        self._emit_comment(line, acknowledge=True)
        self._recent_outputs.append(line)
        if mark_shown:
            self._memory.mark_eod_shown(date_str)
        return True

    async def run_eod_summary(self, which: str = "yesterday") -> str | None:
        """Public async entry for the /summary slash command.

        ``which`` is 'today' or 'yesterday'. Returns a status line for the
        command output. Does NOT mark the day as shown — the slash command
        is on-demand and may be invoked multiple times.
        """
        if self._eod is None:
            return "/summary needs memory enabled."
        date_str = today_str() if which == "today" else yesterday_str()
        emitted = await self._emit_eod_bubble(date_str, mark_shown=False)
        if emitted:
            return None
        return f"Nothing to summarize for {date_str}."

    def _load_previous_session_note(self) -> None:
        """Pull the most recent session summary within the lookback window."""
        if (
            self._memory is None
            or not self._memory.enabled
            or not self._session_summary_config.enabled
        ):
            return
        try:
            lookback_s = self._session_summary_config.max_lookback_h * 3600
            row = self._memory.get_latest_summary(lookback_s)
        except Exception:
            log.debug("get_latest_summary failed", exc_info=True)
            return
        if row is None:
            return
        _ts, text = row
        self._previous_session_note = text
        log.info("Loaded previous session handoff note (%d chars)", len(text))

    def _start_session_summarizer(self) -> None:
        """Spawn the periodic summarizer task; no-op if disabled."""
        if (
            self._memory is None
            or not self._memory.enabled
            or not self._session_summary_config.enabled
        ):
            return
        self._session_summarizer = SessionSummarizer(
            memory=self._memory,
            llm=self._llm,
            interval_s=self._session_summary_config.interval_s,
            target_latency_s=self._budgets.observation,
            min_tokens=self._min_tokens.observation,
        )
        self._session_summary_task = asyncio.create_task(
            self._session_summarizer.run_forever()
        )

    def _compute_first_session_of_day(self) -> bool:
        """True when no prior session_start landed in today's memory.db."""
        if self._memory is None or not self._memory.enabled:
            return True
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            epoch = today.timestamp()
            with self._memory._lock:
                row = self._memory._conn.execute(
                    "SELECT COUNT(*) FROM observations "
                    "WHERE event_type = 'session_start' AND timestamp >= ? "
                    "AND session_id != ?",
                    (epoch, self._memory.session_id),
                ).fetchone()
            return (row[0] if row else 0) == 0
        except Exception:
            log.debug("first_session_of_day probe failed", exc_info=True)
            return True

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

                # Process any pending agent goals (one at a time — agent runs
                # are long-lived and suppress observations while in flight).
                while not self._agent_goal_queue.empty():
                    try:
                        goal = self._agent_goal_queue.get_nowait()
                        await self._handle_agent_goal(goal)
                    except asyncio.QueueEmpty:
                        break

                while not self._research_queue.empty():
                    try:
                        question = self._research_queue.get_nowait()
                        await self._handle_research(question)
                    except asyncio.QueueEmpty:
                        break

                # Fire any due proactive nudges (stretch/water/etc).
                # Respects conversation + sensitive-app gates via _proactive_paused.
                self._proactive.tick()

                # Clean up expired conversation sessions
                if self._conversation and self._conversation.is_expired:
                    log.debug(
                        "Conversation session expired (%.0fs idle, %d turns)",
                        time.monotonic() - self._conversation.last_activity,
                        self._conversation.turn_count,
                    )
                    self._clear_conversation()

                # Sync the intent store with the current foreground app so
                # its dwell timer stays accurate.
                self._sync_intent_app()

                # Rage detector (opt-in) — bypass the comment gate since it
                # fires rarely and the user explicitly enabled the feature.
                rage = self._rage.ingest(readings)

                # Proactive-git: consume readings, then ask the detector.
                # user_present flips to True on any reading this tick — a
                # cheap "computer is live" proxy so we don't nudge into a
                # quiet dark room.
                self._git_nudge.ingest(readings)
                if readings:
                    self._user_present = True
                git_sig = (
                    self._git_nudge.check(user_present=self._user_present)
                    if self._git_nudge.enabled
                    else None
                )

                # High-signal events bypass the normal gate
                has_urgent = any(
                    r.sense_name == "git" and r.changed_from
                    for r in readings
                )
                can_comment = (
                    rage is not None
                    or git_sig is not None
                    or has_urgent
                    or self._should_comment()
                )
                drift = (
                    self._intent.check_drift() if self._intent is not None else None
                )
                emitted = False
                if rage is not None:
                    emitted = await self._generate_rage_check(rage)
                elif git_sig is not None:
                    emitted = await self._generate_git_nudge(git_sig)
                elif drift is not None and can_comment:
                    emitted = await self._generate_drift_nudge(drift)
                elif can_comment:
                    emitted = await self._generate_comment(snapshot)
                elif self._should_freeform():
                    emitted = await self._generate_freeform_comment()

                if not emitted and self._idle_tools_eligible():
                    await self._maybe_fire_idle_tool(snapshot)

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
    def proactive(self) -> ProactiveScheduler:
        """Shared scheduler that drives opt-in focus/health nudges."""
        return self._proactive

    @property
    def ui_callback(self) -> Callable[[str], None]:
        """Expose the UI callback so actions can emit in-character bubbles."""
        return self._ui_callback

    def _inject_brain_deps(self) -> None:
        """Wire the scheduler / memory / ui_callback into actions post-hoc.

        Actions constructed by resolve_actions() don't have the brain yet.
        Rather than threading those through the constructor, we hand them
        off here by name. Actions that don't care about these attrs are
        untouched.
        """
        for action in self._actions.values():
            if hasattr(action, "_scheduler") and getattr(action, "_scheduler") is None:
                action._scheduler = self._proactive  # type: ignore[attr-defined]
            if hasattr(action, "_memory") and getattr(action, "_memory") is None:
                action._memory = self._memory  # type: ignore[attr-defined]
            if hasattr(action, "_llm") and getattr(action, "_llm") is None:
                action._llm = self._llm  # type: ignore[attr-defined]
            if hasattr(action, "_research_config") and getattr(action, "_research_config") is None:
                action._research_config = self._research.config  # type: ignore[attr-defined]
            if hasattr(action, "_ui_callback"):
                current = getattr(action, "_ui_callback", None)
                # Replace the no-op stub from action init with the real cb.
                if current is None or getattr(current, "__name__", "") == "<lambda>":
                    action._ui_callback = self._ui_callback  # type: ignore[attr-defined]

    def _proactive_paused(self) -> bool:
        """Pause proactive nudges during conversation, sensitive apps, or long tasks."""
        if self._paused:
            return True
        if self._any_long_task():
            return True
        if self._in_conversation:
            return True
        snapshot = self._context.snapshot()
        if snapshot and self._personality.check_sensitive_app(snapshot):
            return True
        return False

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

    def _any_long_task(self) -> bool:
        return self._mode is not BrainMode.IDLE

    def _should_comment(self) -> bool:
        if self._paused:
            return False
        if self._in_conversation:
            return False
        if self._any_long_task():
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
        if self._any_long_task():
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
        self._suppressed_streak = 0
        self._comment_timestamps.append(time.monotonic())

    def _handle_suppressed_output(self, reason: str) -> None:
        """Apply cooldown + silence pressure after a filter rejected a gen.

        Without this, the three emit paths leave `_last_comment_time` intact
        after a suppression, so the next tick's gate sees "it's been forever
        since we spoke" and fires another LLM call immediately. That loop
        can burn thousands of generations overnight when the model is stuck
        on a locked phrase (seen 3k+ suppressions in one session).
        """
        now = time.monotonic()
        self._last_comment_time = now
        self._consecutive_comments = 0
        self._suppressed_streak += 1
        if self._suppressed_streak >= _FORCED_SILENCE_AFTER_SUPPRESSIONS:
            log.info(
                "Gate: forced silence for %ds after %d consecutive suppressions (%s)",
                int(_FORCED_SILENCE_DURATION), self._suppressed_streak, reason,
            )
            self._forced_silence_until = now + _FORCED_SILENCE_DURATION
            self._suppressed_streak = 0

    @staticmethod
    def _trigram_set(text: str) -> set[str]:
        normalized = "".join(c.lower() for c in text if c.isalnum() or c.isspace())
        normalized = " ".join(normalized.split())
        if len(normalized) < 3:
            return {normalized}
        return {normalized[i:i + 3] for i in range(len(normalized) - 2)}

    def _is_near_duplicate(self, text: str) -> bool:
        """True if `text` overlaps ≥ _NEAR_DUPLICATE_JACCARD with recent output."""
        if not self._recent_outputs:
            return False
        new_set = self._trigram_set(text)
        if not new_set:
            return False
        for prior in self._recent_outputs:
            prior_set = self._trigram_set(prior)
            if not prior_set:
                continue
            union = new_set | prior_set
            if not union:
                continue
            jaccard = len(new_set & prior_set) / len(union)
            if jaccard >= _NEAR_DUPLICATE_JACCARD:
                log.debug(
                    "Gate: near-duplicate suppressed (jaccard=%.2f vs %r)",
                    jaccard, prior[:60],
                )
                return True
        return self._has_recent_prefix_lock(text)

    @staticmethod
    def _leading_tokens(text: str, n: int = _PREFIX_LOCK_TOKEN_COUNT) -> str:
        """Lowercase, punctuation-stripped first N word-tokens, space-joined."""
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
        return " ".join(cleaned.split()[:n])

    def _has_recent_prefix_lock(self, text: str) -> bool:
        """True if `text` shares its leading N tokens with M+ recent outputs.

        Catches template drift where a voice anchors on one lead phrase and
        varies only the tail ('Jake, good cop... this X got more Y than Z').
        Surface Jaccard misses these because the tail carries most trigrams.
        """
        prefix = self._leading_tokens(text)
        if not prefix:
            return False
        matches = sum(
            1 for prior in self._recent_outputs
            if self._leading_tokens(prior) == prefix
        )
        if matches >= _PREFIX_LOCK_MIN_MATCHES:
            log.info(
                "Gate: prefix-lock suppressed %r (%d matches in last %d)",
                prefix, matches, len(self._recent_outputs),
            )
            return True
        return False

    def _sync_intent_app(self) -> None:
        """Notify IntentStore when the foreground app changes so its dwell
        timer stays accurate. Cheap on-tick call; no-op when intent is off.
        """
        if self._intent is None:
            return
        current = self._personality._last_seen_app
        if current and current != self._last_app_for_intent:
            self._intent.on_app_change(current)
            self._last_app_for_intent = current

    @property
    def intent(self) -> IntentStore | None:
        """Public accessor for the `/intent` slash command wiring."""
        return self._intent

    async def _generate_drift_nudge(self, drift: DriftSignal) -> bool:
        """Emit a single in-character nudge about the drifted-from intent.

        The drift detector has its own 10min cooldown (see IntentStore);
        this path additionally rides the normal pacing gate in _run_loop.
        """
        assert self._intent is not None, "drift signal implies intent is on"
        if self._personality.check_sensitive_app(
            self._context.snapshot()
        ):
            # User drifted into a non-sensitive distraction app but may have
            # a sensitive app in context too — stay silent to be safe.
            return False
        prompt = self._personality.build_drift_nudge_prompt(
            intent_text=drift.intent_text,
            app_name=drift.app_name,
            dwell_s=drift.dwell_s,
        )
        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug(
                "Generating drift nudge: intent=%r, app=%r, dwell=%.0fs",
                drift.intent_text,
                drift.app_name,
                drift.dwell_s,
            )
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._budgets.observation,
                min_tokens=self._min_tokens.observation,
            )
            self._push_status()
            filtered = self._personality.filter_response(response.text)
            if filtered and self._is_near_duplicate(filtered):
                log.info("TokenPal (drift suppressed near-duplicate): %s", filtered)
                self._handle_suppressed_output("drift near-duplicate")
                return False
            if filtered:
                log.info(
                    "TokenPal (drift): %s (%.0fms)", filtered, response.latency_ms
                )
                self._emit_comment(filtered, acknowledge=True)
                self._recent_outputs.append(filtered)
                self._intent.mark_drift_emitted()
                return True
            log.debug("Drift nudge filtered out: %r", response.text[:80])
            return False
        except Exception:
            log.exception("Drift nudge generation failed")
            self._push_status()
            return False

    async def _generate_git_nudge(self, sig: GitNudgeSignal) -> bool:
        """Emit a single in-character nudge about a stale WIP commit."""
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            # Sensitive-app context suppresses all commentary; still start
            # the cooldown so we don't busy-loop.
            self._git_nudge.mark_emitted()
            return False
        prompt = self._personality.build_git_nudge_prompt(
            branch=sig.branch,
            commit_msg=sig.last_commit_msg,
            stale_hours=sig.stale_hours,
        )
        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug(
                "Generating git nudge: branch=%r, commit=%r, stale=%.0fh",
                sig.branch,
                sig.last_commit_msg,
                sig.stale_hours,
            )
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._budgets.observation,
                min_tokens=self._min_tokens.observation,
            )
            self._push_status()
            filtered = self._personality.filter_response(response.text)
            if filtered and self._is_near_duplicate(filtered):
                log.info(
                    "TokenPal (git-nudge suppressed near-duplicate): %s", filtered
                )
                self._handle_suppressed_output("git-nudge near-duplicate")
                self._git_nudge.mark_emitted()
                return False
            if filtered:
                log.info(
                    "TokenPal (git nudge): %s (%.0fms)",
                    filtered,
                    response.latency_ms,
                )
                self._emit_comment(filtered, acknowledge=True)
                self._recent_outputs.append(filtered)
                self._git_nudge.mark_emitted()
                return True
            log.debug("Git nudge filtered out: %r", response.text[:80])
            self._git_nudge.mark_emitted()
            return False
        except Exception:
            log.exception("Git nudge generation failed")
            self._push_status()
            return False

    async def _generate_rage_check(self, rage: RageSignal) -> bool:
        """Emit a single in-character check-in after a rage-quit pattern."""
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            # Never trigger a mental-health-adjacent nudge into a sensitive
            # app context.
            self._rage.mark_emitted()  # still start cooldown so we don't loop
            return False
        prompt = self._personality.build_rage_check_prompt(rage.app_name)
        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug(
                "Generating rage check: app=%r, pause=%.0fs",
                rage.app_name,
                rage.pause_s,
            )
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._budgets.observation,
                min_tokens=self._min_tokens.observation,
            )
            self._push_status()
            filtered = self._personality.filter_response(response.text)
            if filtered and self._is_near_duplicate(filtered):
                log.info("TokenPal (rage suppressed near-duplicate): %s", filtered)
                self._handle_suppressed_output("rage near-duplicate")
                self._rage.mark_emitted()
                return False
            if filtered:
                log.info(
                    "TokenPal (rage): %s (%.0fms)", filtered, response.latency_ms
                )
                self._emit_comment(filtered, acknowledge=True)
                self._recent_outputs.append(filtered)
                self._rage.mark_emitted()
                return True
            log.debug("Rage check filtered out: %r", response.text[:80])
            self._rage.mark_emitted()
            return False
        except Exception:
            log.exception("Rage check generation failed")
            self._push_status()
            return False

    async def _generate_freeform_comment(self) -> bool:
        """Generate an unprompted in-character thought. Returns True iff emitted."""
        prompt = self._personality.build_freeform_prompt()

        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug("Generating freeform comment...")
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._budgets.freeform,
                min_tokens=self._min_tokens.freeform,
            )
            self._push_status()

            filtered = self._personality.filter_response(response.text)
            if filtered and self._is_near_duplicate(filtered):
                log.info("TokenPal (freeform suppressed near-duplicate): %s", filtered)
                self._handle_suppressed_output("freeform near-duplicate")
                return False

            if filtered:
                log.info("TokenPal (freeform): %s (%.0fms)", filtered, response.latency_ms)
                self._emit_comment(filtered)
                self._recent_outputs.append(filtered)
                return True
            log.debug("Freeform filtered out: %r", response.text[:80])
            return False

        except Exception:
            log.exception("Freeform generation failed")
            self._push_status()
            return False

    # ------------------------------------------------------------------
    # Idle-tool roll — third emission path
    # ------------------------------------------------------------------

    def _idle_tools_eligible(self) -> bool:
        """Same gates that silence observations also silence idle rolls."""
        if not self._idle_tools_config.enabled:
            return False
        if self._paused:
            return False
        if self._in_conversation:
            return False
        if self._any_long_task():
            return False
        if time.monotonic() < self._forced_silence_until:
            return False
        return True

    def _build_idle_context(self) -> Any:
        return build_context(
            now=datetime.now(),
            session_minutes=int(
                (time.monotonic() - self._session_started_at) / 60
            ),
            first_session_of_day=self._first_session_of_day,
            active_readings=self._context.active_readings(),
            mood=str(self._personality.mood),
            time_since_last_comment_s=time.monotonic() - self._last_comment_time,
            consent_web_fetches=has_consent(Category.WEB_FETCHES),
        )

    async def _maybe_fire_idle_tool(self, snapshot: str) -> None:
        """Roll the idle-tool die; on hit, riff the result in-character."""
        if self._personality.check_sensitive_app(snapshot):
            return
        ctx = self._build_idle_context()
        try:
            result = await self._idle_tools.maybe_fire(ctx)
        except Exception:
            log.exception("Idle tool roll crashed")
            return
        if result is None:
            return
        if result.running_bit:
            self._register_running_bit(result)
            if not result.opener_framing:
                # Silent registration — bit rides along future prompts without
                # announcing itself. Still counts as a fire for telemetry.
                self._record_idle_fire(result, emitted=True)
                return
        await self._generate_tool_riff(snapshot, result)

    def _register_running_bit(self, fire: IdleFireResult) -> None:
        """Install the fired rule as a running bit on the personality engine."""
        try:
            framing = fire.framing.format(output=fire.tool_output)
        except (KeyError, IndexError):
            framing = fire.framing
        self._personality.add_running_bit(
            tag=fire.rule_name,
            framing=framing,
            decay_s=fire.bit_decay_s,
            payload={"output": fire.tool_output},
        )

    async def _generate_tool_riff(
        self, snapshot: str, fire: IdleFireResult,
    ) -> None:
        """Compose an in-character line that weaves the tool output in."""
        # Running-bit opener uses opener_framing; one-shot rules use framing.
        framing = fire.opener_framing if fire.running_bit else fire.framing
        detail_block = fire.tool_output
        if fire.extra_outputs:
            detail_lines = [fire.tool_output]
            for tool_name, extra in fire.extra_outputs.items():
                detail_lines.append(f"({tool_name}) {extra}")
            detail_block = "\n".join(detail_lines)
        prompt = (
            f"{self._personality.build_freeform_prompt()}\n\n"
            f"[Current moment:]\n{snapshot}\n\n"
            f"[Fresh detail to weave in, in-character:]\n{detail_block}\n\n"
            f"[How to frame it:]\n{framing}\n"
        )
        try:
            if self._status_callback:
                self._status_callback("thinking...")
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._budgets.idle_tool,
                min_tokens=self._min_tokens.idle_tool,
            )
            self._push_status()
        except Exception:
            log.exception("Idle-tool riff generation failed")
            self._push_status()
            self._record_idle_fire(fire, emitted=False)
            return

        filtered = self._personality.filter_response(response.text)
        if filtered and self._is_near_duplicate(filtered):
            log.info(
                "TokenPal (idle-tool %s suppressed near-duplicate): %s",
                fire.rule_name, filtered,
            )
            self._handle_suppressed_output(f"idle-tool {fire.rule_name}")
            filtered = ""

        if not filtered:
            log.debug(
                "Idle-tool riff filtered out: %r",
                response.text[:80] if response.text else "",
            )
            self._record_idle_fire(fire, emitted=False)
            return

        log.info(
            "TokenPal (idle-tool %s -> %s): %s (%.0fms)",
            fire.rule_name, fire.tool_name, filtered, response.latency_ms,
        )
        self._emit_comment(filtered)
        self._recent_outputs.append(filtered)
        self._record_idle_fire(fire, emitted=True)

    def _record_idle_fire(self, fire: IdleFireResult, *, emitted: bool) -> None:
        """Write a telemetry row so memory_query can surface idle-tool stats."""
        if self._memory is None:
            return
        try:
            self._memory.record_observation(
                sense_name="idle_tools",
                event_type="idle_tool_fire",
                summary=fire.rule_name,
                data={
                    "tool": fire.tool_name,
                    "emitted": emitted,
                    "tool_success": fire.success,
                    "running_bit": fire.running_bit,
                    "latency_ms": int(fire.latency_ms),
                },
            )
        except Exception:
            log.debug("idle_tool_fire telemetry write failed", exc_info=True)

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

    async def _maybe_enrich_snapshot(self, snapshot: str) -> str:
        """Splice the active app's description into the snapshot's App: line."""
        if self._app_enricher is None:
            return snapshot
        app_reading = self._context.active_readings().get("app_awareness")
        if app_reading is None:
            return snapshot
        app_name = (app_reading.data or {}).get("app_name")
        if not app_name:
            return snapshot
        description = await self._app_enricher.enrich(app_name)
        if not description:
            return snapshot
        return snapshot.replace(
            f"App: {app_name}", f"App: {app_name} ({description})", 1,
        )

    async def _generate_comment(self, snapshot: str | None = None) -> bool:
        """Generate an observation comment. Returns True iff a line was emitted.

        False return lets the brain loop fall through to the idle-tool roller
        on the same tick, so suppressed near-duplicates don't starve idle
        rolls (observations would otherwise consume every tick).
        """
        if snapshot is None:
            snapshot = self._context.snapshot()
        if not snapshot.strip():
            return False

        # Guardrail: sensitive app detected — go silent
        if self._personality.check_sensitive_app(snapshot):
            log.debug("Sensitive app detected — staying silent")
            return False

        # Check for easter eggs first — bypass LLM entirely
        egg = self._personality.check_easter_egg(snapshot)
        if egg:
            log.info("TokenPal (easter egg): %s", egg)
            self._emit_comment(egg, acknowledge=True)
            return True

        snapshot = await self._maybe_enrich_snapshot(snapshot)

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
            snapshot,
            memory_lines=memory_lines,
            callback_lines=callback_lines,
            previous_session=self._previous_session_note,
        )

        try:
            if self._status_callback:
                self._status_callback("thinking...")
            log.debug("Generating observation comment...")
            # Use tool-calling path if actions are available
            if self._actions and self._tool_specs:
                response = await self._generate_with_tools(
                    prompt,
                    target_latency_s=self._budgets.tools,
                    min_tokens=self._min_tokens.tools,
                )
            else:
                response = await self._llm.generate(
                    prompt,
                    target_latency_s=self._budgets.observation,
                    min_tokens=self._min_tokens.observation,
                )
            self._push_status()

            if not response.text:
                log.debug("LLM returned empty content (model may need higher max_tokens)")
            filtered = self._personality.filter_response(response.text)

            if filtered and self._is_near_duplicate(filtered):
                log.info("TokenPal (suppressed near-duplicate): %s", filtered)
                self._handle_suppressed_output("observation near-duplicate")
                return False

            if filtered:
                log.info("TokenPal says: %s (%.0fms)", filtered, response.latency_ms)
                # Re-enable tool specs if they were disabled due to failures
                if self._consecutive_failures > 0 and self._actions and not self._tool_specs:
                    self._tool_specs = [a.to_tool_spec() for a in self._actions.values()]
                    log.info("Re-enabled tool-calling after successful generation")
                self._consecutive_failures = 0
                self._emit_comment(filtered, acknowledge=True)
                self._recent_outputs.append(filtered)
                self._recent_topics.append(topic)
                # Record comment milestones
                if self._memory and self._personality._total_comments % 10 == 0:
                    self._memory.record_observation(
                        "system", "milestone",
                        f"Comment #{self._personality._total_comments}",
                    )
                return True
            else:
                log.debug("LLM chose silence")
                self._consecutive_comments = 0
                return False

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
                return True
            return False

    def _effective_conv_max_tokens(self) -> int:
        """Conversation response budget: user-pinned wins, else server-derived, else 300."""
        if self._conv_config.max_response_tokens > 0:
            return self._conv_config.max_response_tokens
        derived = getattr(self._llm, "derived_max_tokens", None)
        return int(derived) if derived else 300

    async def _generate_with_tools(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> LLMResponse:
        """Multi-turn tool-calling loop. Sends prompt (or pre-built messages)
        with tool defs, executes tool calls, feeds results back, and repeats
        until the LLM produces a final text response (or we hit the round limit).

        When ``target_latency_s`` is set, each round gets the remaining
        wall-clock budget (deadline - now), not a static fraction. An
        early-fast round thus leaves the next round more runway. The final
        forced-text-only round is capped at the floor to keep the reply
        coherent even when the budget has already been spent.
        """
        if messages is None:
            assert prompt is not None
            messages = [{"role": "user", "content": prompt}]

        deadline = (
            time.monotonic() + target_latency_s
            if target_latency_s is not None
            else None
        )

        def _remaining() -> float | None:
            return max(0.0, deadline - time.monotonic()) if deadline is not None else None

        for _round in range(self._MAX_TOOL_ROUNDS):
            response = await self._llm.generate_with_tools(
                messages=messages,
                tools=self._tool_specs,
                max_tokens=max_tokens,
                target_latency_s=_remaining(),
                min_tokens=min_tokens,
            )

            if not response.tool_calls:
                return response

            if log.isEnabledFor(logging.DEBUG):
                for tc in response.tool_calls:
                    log.debug("Tool round %d: %s(%s)", _round, tc.name, fmt_args(tc.arguments))

            messages.append(response.to_assistant_message())

            if self._status_callback:
                names = ", ".join(tc.name for tc in response.tool_calls)
                self._status_callback(f"using {names}...")

            results = await asyncio.gather(
                *(self._execute_tool_call(tc) for tc in response.tool_calls),
            )
            # Leave "using X..." visible through the follow-up LLM round so a
            # fast gather isn't overwritten before the UI can render it. The
            # caller clears status via _push_status once the full reply lands.
            for tc, result_text in zip(response.tool_calls, results):
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("Tool round %d result [%s]: %.200s", _round, tc.name, result_text)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        log.warning("Hit tool round limit (%d), forcing text response", self._MAX_TOOL_ROUNDS)
        return await self._llm.generate_with_tools(
            messages=messages,
            tools=[],
            max_tokens=max_tokens,
            target_latency_s=_remaining(),
            min_tokens=min_tokens,
        )

    async def _reply_with_continuation(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> str:
        """Generate a conversation reply, auto-continuing when the LLM hits
        max_tokens mid-thought. Concatenates segments and, as a final safety
        net, trims any still-ragged tail back to the last sentence boundary."""
        work = list(messages)
        pieces: list[str] = []
        use_tools = bool(self._actions and self._tool_specs)
        for attempt in range(self._MAX_CONTINUATIONS + 1):
            if use_tools:
                response = await self._generate_with_tools(
                    messages=work, max_tokens=max_tokens,
                )
            else:
                response = await self._llm.generate_with_tools(
                    messages=work, tools=[], max_tokens=max_tokens,
                )
            piece = response.text or ""
            pieces.append(piece)
            if response.finish_reason != "length" or not piece:
                break
            if attempt == self._MAX_CONTINUATIONS:
                log.info(
                    "Conversation reply still truncated after %d continuations",
                    self._MAX_CONTINUATIONS,
                )
                break
            log.info(
                "Conversation reply hit max_tokens, continuing (round %d)",
                attempt + 1,
            )
            work = [*work, {"role": "assistant", "content": piece}]

        full = "".join(pieces)
        if full and not _ends_with_sentence(full):
            trimmed = _trim_to_last_sentence(full)
            if trimmed:
                full = trimmed + "…"
        return full

    async def _execute_tool_call(self, tc: ToolCall) -> str:
        """Execute a single tool call and return the result text."""
        action = self._actions.get(tc.name)
        if action is None:
            log.warning("LLM called unknown action: %s", tc.name)
            return f"Unknown tool '{tc.name}'."
        try:
            result = await action.execute(**tc.arguments)
            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "Action '%s'(%s) -> %.200s",
                    tc.name, fmt_args(tc.arguments), result.output,
                )
            if result.display_text and self._log_callback:
                self._log_callback(result.display_text)
            if result.display_url and self._log_callback:
                self._log_callback("Source:", url=result.display_url)
            if result.display_urls and self._log_callback:
                for label, url in result.display_urls:
                    self._log_callback(label, url=url)
            return result.output
        except Exception as e:
            log.warning("Action '%s' failed: %s", tc.name, e)
            return f"Error: {e}"

    def _post_threadsafe(self, queue: asyncio.Queue[str], item: str, label: str) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            log.warning("Brain event loop closed — %s dropped", label)

    def submit_user_input(self, text: str) -> None:
        self._post_threadsafe(self._user_input_queue, text, "user input")

    def submit_agent_goal(self, goal: str) -> None:
        self._post_threadsafe(self._agent_goal_queue, goal, "agent goal")

    def submit_research_question(self, question: str) -> None:
        self._post_threadsafe(self._research_queue, question, "research question")

    @property
    def mode(self) -> BrainMode:
        return self._mode

    @property
    def agent_running(self) -> bool:
        return self._mode is BrainMode.AGENT

    @property
    def research_running(self) -> bool:
        return self._mode is BrainMode.RESEARCH

    async def _handle_agent_goal(self, goal: str) -> AgentSession:
        """Run one agent session from start to finish. Suppresses
        observations + freeform for the duration and swaps to the agent
        model if configured."""
        if self._agent.log_callback is None or self._agent.confirm_callback is None:
            self._ui_callback(
                "/agent isn't wired up — the overlay can't show confirm modals yet."
            )
            return AgentSession(goal=goal, stopped_reason=AgentStopReason.UNAVAILABLE)

        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            self._ui_callback("Not now — sensitive window is open.")
            return AgentSession(goal=goal, stopped_reason=AgentStopReason.SENSITIVE)

        runner = AgentRunner(
            llm=self._llm,
            actions=self._actions,
            tool_specs=self._tool_specs,
            log_callback=self._agent.log_callback,
            confirm_callback=self._agent.confirm_callback,
            is_sensitive=self._sensitive_check,
            status_callback=self._status_callback,
            max_steps=self._agent.config.max_steps,
            token_budget=self._agent.config.token_budget,
            per_step_timeout_s=self._agent.config.per_step_timeout_s,
            invoker=self._build_invoker(),
        )

        previous_model = self._llm.model_name
        swapped = False
        target_model = self._agent.config.model.strip()
        if target_model and target_model != previous_model:
            try:
                self._llm.set_model(target_model)
                swapped = True
                log.info("Agent model swapped: %s -> %s", previous_model, target_model)
            except NotImplementedError:
                log.debug("Backend does not support model swap — staying on %s", previous_model)

        self._mode = BrainMode.AGENT
        if self._status_callback:
            self._status_callback("agent running...")
        self._agent.log_callback(f"> agent: {goal}")
        try:
            session = await runner.run(goal)
        except Exception:
            log.exception("Agent run crashed")
            session = AgentSession(goal=goal, stopped_reason=AgentStopReason.CRASHED)
        finally:
            self._mode = BrainMode.IDLE
            if swapped:
                try:
                    self._llm.set_model(previous_model)
                except NotImplementedError:
                    pass
            self._push_status()

        summary = _format_agent_summary(session)
        final = session.final_text.strip() or summary
        self._agent.log_callback(f"= {summary}")
        self._ui_callback(final)
        self._last_comment_time = time.monotonic()
        return session

    def _sensitive_check(self) -> bool:
        """Runner-facing predicate — re-check on every agent step."""
        return self._personality.check_sensitive_app(self._context.snapshot())

    def _build_invoker(self) -> ToolInvoker:
        on_call = None
        if self._memory is not None and self._memory.enabled:
            memory = self._memory

            def _record(name: str, duration_ms: float, success: bool) -> None:
                memory.record_tool_call(name, duration_ms, success)

            on_call = _record
        return ToolInvoker(on_call=on_call)

    async def _handle_research(self, question: str) -> ResearchSession:
        """Run one /research pipeline. Log callback routes to chat log via
        the agent log sink (same stream — trace lines, not speech bubbles)."""
        log_cb = self._agent.log_callback or (lambda _s: None)

        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            self._ui_callback("Not now — sensitive window is open.")
            return ResearchSession(
                question=question, stopped_reason=ResearchStopReason.UNAVAILABLE
            )

        cached = self._load_research_cache(question)
        if cached is not None:
            log_cb(f"> research: {question} (cached)")
            self._ui_callback(cached.answer)
            self._last_comment_time = time.monotonic()
            return cached

        from tokenpal.actions.research.fetch_url import fetch_and_extract

        async def _fetch(url: str) -> str | None:
            try:
                return await fetch_and_extract(
                    url, timeout_s=self._research.config.per_fetch_timeout_s
                )
            except Exception:
                log.exception("fetch_and_extract raised during research")
                return None

        previous_model = self._llm.model_name
        active_model = previous_model
        target = (
            self._research.config.planner_model.strip()
            or self._research.config.synth_model.strip()
        )
        swapped = False
        if target and target != previous_model:
            try:
                self._llm.set_model(target)
                active_model = target
                swapped = True
                log.info("Research model swapped: %s -> %s", previous_model, target)
            except NotImplementedError:
                log.debug("Backend does not support model swap")

        runner = ResearchRunner(
            llm=self._llm,
            fetch_url=_fetch,
            log_callback=log_cb,
            status_callback=self._status_callback,
            max_queries=self._research.config.max_queries,
            max_fetches=self._research.config.max_fetches,
            token_budget=self._research.config.token_budget,
            per_search_timeout_s=self._research.config.per_search_timeout_s,
            per_fetch_timeout_s=self._research.config.per_fetch_timeout_s,
            synth_thinking=self._research.config.synth_thinking,
        )

        self._mode = BrainMode.RESEARCH
        if self._status_callback:
            self._status_callback("researching...")
        log_cb(f"> research: {question}")
        try:
            session = await runner.run(question)
        except Exception:
            log.exception("Research run crashed")
            session = ResearchSession(
                question=question, stopped_reason=ResearchStopReason.CRASHED
            )
        finally:
            self._mode = BrainMode.IDLE
            if swapped:
                try:
                    self._llm.set_model(previous_model)
                except NotImplementedError:
                    pass
            self._push_status()

        log.debug("Research used model %s (%d tokens)", active_model, session.tokens_used)

        summary = _format_research_summary(session)
        log_cb(f"= {summary}")
        final = (session.answer or summary).strip()
        self._ui_callback(final)
        self._last_comment_time = time.monotonic()
        if session.is_complete:
            self._save_research_cache(question, session)
        return session

    def _research_cache_key(self, question: str) -> str:
        return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()

    def _research_cache_ttl(self) -> float | None:
        """Return the cache TTL in seconds, or None when the cache is off."""
        if self._memory is None or not self._memory.enabled:
            return None
        ttl = self._research.config.cache_ttl_s
        return ttl if ttl > 0 else None

    def _load_research_cache(self, question: str) -> ResearchSession | None:
        ttl = self._research_cache_ttl()
        if ttl is None:
            return None
        assert self._memory is not None
        hit = self._memory.get_research_answer(
            self._research_cache_key(question), max_age_s=ttl
        )
        if hit is None:
            return None
        answer, sources_json, age = hit
        try:
            payload = json.loads(sources_json)
        except (TypeError, ValueError):
            payload = []
        sources = [
            Source(
                number=int(s.get("number", i + 1)),
                url=str(s.get("url", "")),
                title=str(s.get("title", "")),
                excerpt=str(s.get("excerpt", "")),
                backend=str(s.get("backend", "")),
            )
            for i, s in enumerate(payload)
        ]
        prefix = _format_cache_age(age)
        return ResearchSession(
            question=question,
            sources=sources,
            answer=f"{prefix} {answer}",
            stopped_reason=ResearchStopReason.COMPLETE,
        )

    def _save_research_cache(self, question: str, session: ResearchSession) -> None:
        if self._research_cache_ttl() is None:
            return
        assert self._memory is not None
        payload = json.dumps([
            {
                "number": s.number,
                "url": s.url,
                "title": s.title,
                "excerpt": s.excerpt,
                "backend": s.backend,
            }
            for s in session.sources
        ])
        self._memory.cache_research_answer(
            self._research_cache_key(question),
            question,
            session.answer,
            payload,
        )

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
        system_msg = self._personality.build_conversation_system_message(
            tool_names=list(self._actions.keys()),
        )
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
            effective_max_tokens = self._effective_conv_max_tokens()
            reply_text = await self._reply_with_continuation(
                messages, effective_max_tokens,
            )
            self._push_status()

            filtered = self._personality.filter_conversation_response(reply_text)
            if filtered:
                char_cap = effective_max_tokens * 4 * (self._MAX_CONTINUATIONS + 1)
                if len(filtered) > char_cap:
                    log.info(
                        "Conversation response %d chars > cap %d — truncating "
                        "(likely LLM drift)",
                        len(filtered), char_cap,
                    )
                    filtered = filtered[: char_cap - 3] + "..."
                self._conversation.add_assistant_turn(filtered)
                log.info("TokenPal (reply): %s", filtered)
                self._personality.record_comment(filtered)
                self._ui_callback(filtered)
                self._last_comment_time = time.monotonic()
            else:
                # Record placeholder so history stays coherent
                self._conversation.add_assistant_turn("[no response]")
                log.debug("Conversation response filtered: %r", reply_text[:80])
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


_STOP_REASON_LABELS: dict[AgentStopReason, str] = {
    AgentStopReason.COMPLETE: "done",
    AgentStopReason.STEP_CAP: "hit step cap",
    AgentStopReason.TOKEN_BUDGET: "hit token budget",
    AgentStopReason.SENSITIVE: "stopped, sensitive window",
    AgentStopReason.DENIED: "stopped, user denied a tool",
    AgentStopReason.TIMEOUT: "stopped, step timed out",
    AgentStopReason.CRASHED: "stopped, crashed",
    AgentStopReason.UNAVAILABLE: "stopped, agent bridge unavailable",
}


_RESEARCH_REASON_LABELS: dict[ResearchStopReason, str] = {
    ResearchStopReason.COMPLETE: "done",
    ResearchStopReason.NO_QUERIES: "stopped, planner emitted no queries",
    ResearchStopReason.NO_SOURCES: "stopped, no usable sources",
    ResearchStopReason.TOKEN_BUDGET: "hit token budget",
    ResearchStopReason.TIMEOUT: "stopped, timed out",
    ResearchStopReason.CRASHED: "stopped, crashed",
    ResearchStopReason.UNAVAILABLE: "stopped, unavailable",
}


def _format_session_summary(
    session: Any,
    labels: dict[Any, str],
    counts: list[tuple[str, int]],
) -> str:
    duration_s = time.monotonic() - session.started_at
    reason = labels.get(
        session.stopped_reason, str(session.stopped_reason) or "unknown"
    )
    tail = ", ".join(f"{n} {name}" for name, n in counts)
    return f"{reason} in {duration_s:.1f}s ({tail}, {session.tokens_used} tokens)"


def _format_cache_age(age_s: float) -> str:
    if age_s < 60:
        return "(cached just now)"
    if age_s < 3600:
        return f"(cached {int(age_s / 60)}m ago)"
    if age_s < 86400:
        return f"(cached {int(age_s / 3600)}h ago)"
    return f"(cached {int(age_s / 86400)}d ago)"


def _format_research_summary(session: ResearchSession) -> str:
    return _format_session_summary(
        session,
        _RESEARCH_REASON_LABELS,
        [("quer(ies)", len(session.queries)), ("source(s)", len(session.sources))],
    )


def _format_agent_summary(session: AgentSession) -> str:
    return _format_session_summary(
        session, _STOP_REASON_LABELS, [("step(s)", len(session.steps))]
    )

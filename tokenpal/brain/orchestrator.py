"""The Brain — central orchestrator.

Polls senses, feeds the LLM, and decides when to comment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlparse

if TYPE_CHECKING:
    from tokenpal.audio.pipeline import AudioPipeline
    from tokenpal.ui.buddy_environment import EnvironmentSnapshot

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.audio.types import InputSource
from tokenpal.brain.agent import AgentRunner, AgentSession, fmt_args
from tokenpal.brain.app_enricher import AppEnricher
from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.eod_summary import EODSummary, today_str, yesterday_str
from tokenpal.brain.idle_tools import IdleFireResult, IdleToolRoller, build_context
from tokenpal.brain.idle_tools_m3 import LLMInitiatedRoller
from tokenpal.brain.intent import DriftSignal, IntentStore
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.news_buffer import (
    NEWS_SOURCES,
    NewsBuffer,
    NewsItem,
    extract_news_items,
)
from tokenpal.brain.observation_enricher import ObservationEnricher
from tokenpal.brain.personality import SENSITIVE_APPS, PersonalityEngine
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.research import ResearchRunner, ResearchSession, Source
from tokenpal.brain.research_followup import FollowupSession
from tokenpal.brain.session_summarizer import SessionSummarizer
from tokenpal.brain.stop_reason import AgentStopReason, ResearchStopReason
from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    PromptContext,
    Wedge,
    WedgeRegistry,
)
from tokenpal.brain.wedges.git_nudge import GitNudgeWedge
from tokenpal.brain.wedges.rage import RageWedge
from tokenpal.config.consent import Category, has_consent
from tokenpal.config.schema import (
    AgentConfig,
    CloudLLMConfig,
    CloudSearchConfig,
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

_QT = TypeVar("_QT")


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
    # Most recent user-turn origin. Reply routing reads this to decide
    # whether to speak the assistant turn — voice-initiated session →
    # speak; typed turn → text only, even mid-session.
    last_user_source: InputSource = "typed"

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

    def add_user_turn(self, content: str, source: InputSource = "typed") -> None:
        self.last_activity = time.monotonic()
        self.last_user_source = source
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

_CONTEXT_LOG_HEARTBEAT_S = 30.0

# Conversation-only suppression window. Decoupled from _RECENT_OUTPUTS_MAX so
# observations don't poison the conv suppression check (and vice versa).
_CONV_RECENT_OUTPUTS_MAX = 5

# Appended to the conversation system message when retrying after a near-dup
# trip. Observation context is stripped on the retry to break the lock.
_RETRY_NEAR_DUP_INSTRUCTION = (
    "\n\nIMPORTANT: Your previous reply was too similar to recent output. "
    "Rephrase with fresh wording. Avoid the opening structure you just used."
)

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
    cloud_config: CloudLLMConfig | None = None
    cloud_search_config: CloudSearchConfig | None = None
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
    "world_awareness": "Pick one of the top HN headlines shown and react to it.",
    "lobsters": "Pick one of the top Lobsters stories shown and react to it.",
    "github_trending": "Pick one of the trending GitHub repos shown and react to it.",
    "sun_position": "React to the current solar phase shown in the snapshot.",
    "process_heat": "Call out which process is hogging the CPU.",
    "typing_cadence": "Comment on their typing rhythm or recent burst.",
    "network_state": "Comment on the network change.",
    "battery": "Comment on the battery state or transition.",
    "filesystem_pulse": "Comment on what they just dropped in or pulled out of a watched folder.",
    "git": "Comment on their recent git activity.",
}


class Brain:
    """Polls senses, builds context, decides when to comment, generates via LLM."""

    # Max tool call rounds per comment to prevent infinite loops.
    # Matches the agent-mode step cap so research + a couple of follow-up
    # fetch_url calls all fit in one conversation turn.
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
        user_log_callback: Callable[[str], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        mood_callback: Callable[[str], None] | None = None,
        news_callback: Callable[[list[NewsItem]], None] | None = None,
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
        audio_pipeline: AudioPipeline | None = None,
    ) -> None:
        # User input queue (thread-safe, fed from main thread). Carries
        # (text, source) so voice-vs-typed routing survives the cross-thread
        # hop without a side channel.
        self._user_input_queue: asyncio.Queue[tuple[str, InputSource]] = (
            asyncio.Queue()
        )
        self._agent_goal_queue: asyncio.Queue[str] = asyncio.Queue()
        self._research_queue: asyncio.Queue[str] = asyncio.Queue()
        self._refine_queue: asyncio.Queue[str] = asyncio.Queue()
        self._followup_queue: asyncio.Queue[str] = asyncio.Queue()
        # Physical-reaction events (overlay → brain). Items are "poke"/"shake".
        self._buddy_event_queue: asyncio.Queue[str] = asyncio.Queue()
        # Last time a buddy-reaction bubble was emitted — 5s cooldown so
        # click-spam can't flood the bubble queue. Separate from the
        # observation-path rate gate.
        self._last_buddy_reaction_time: float = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._senses = senses
        self._llm = llm
        self._ui_callback = ui_callback
        # Voice transcripts bypass the overlay's typed-input handler, so
        # they need a parallel hook to land in the chat history. Typed input
        # is already logged by the overlay before reaching the brain.
        self._user_log_callback = user_log_callback
        self._log_callback = log_callback
        # Audio pipeline drives ambient TTS. None when both [audio] toggles
        # are off or the install path hasn't run — _emit_comment becomes a
        # no-op for speech in that case, text bubbles still render.
        self._audio_pipeline = audio_pipeline
        # Edge-detect sensitive-app transitions so the wake listener pauses
        # only when state changes, not on every tick.
        self._was_sensitive_app: bool = False
        self._personality = personality
        self._status_callback = status_callback
        self._mood_callback = mood_callback
        self._news_callback = news_callback
        self._news_buffer = NewsBuffer()
        self._last_mood_role: str = self._personality.mood_role
        self._memory = memory
        self._last_recorded_app: str = ""
        self._poll_interval = poll_interval_s
        self._cooldown = comment_cooldown_s
        self._threshold = interestingness_threshold
        self._context = ContextWindowBuilder(max_tokens=context_max_tokens)
        self._last_comment_time: float = time.monotonic()
        self._last_context_log: str | None = None
        self._last_context_log_at: float = 0.0
        self._log_context_full = os.environ.get("TOKENPAL_LOG_CONTEXT_FULL") == "1"
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
        # Conv-only mirror. Observation pollution would make a fresh chat reply
        # look like a duplicate, so the conv suppression check reads from here.
        self._conversation_recent_outputs: deque[str] = deque(
            maxlen=_CONV_RECENT_OUTPUTS_MAX
        )

        # Conversation session state
        self._conversation: ConversationSession | None = None
        self._conv_config = conversation or ConversationConfig()

        # Cloud /research follow-up state. One active session per Brain —
        # newer /research overwrites the slot. Expired lazily at read.
        # See plans/shipped/smarter-buddy.md (Option 2: single-slot scoping).
        self._active_followup_session: FollowupSession | None = None

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
        # M3 (issue #33). Shares the deterministic roller's tracker so that
        # cross-path cooldowns work (a deterministic moon_phase fire blocks
        # M3 moon_phase for the rule's cooldown window).
        self._idle_tools_m3 = LLMInitiatedRoller(
            config=self._idle_tools_config,
            actions=self._actions,
            llm=self._llm,
            tracker=self._idle_tools.tracker,
        )
        # Session-scoped: computed once at startup from memory.db.
        self._first_session_of_day: bool = True
        self._session_started_at: float = time.monotonic()

        self._app_enricher = (
            AppEnricher(memory=self._memory) if self._memory is not None else None
        )
        self._observation_enricher = (
            ObservationEnricher(app_enricher=self._app_enricher)
            if self._app_enricher is not None
            else None
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

        self._git_nudge_wedge = GitNudgeWedge(
            config=git_nudge_config or GitNudgeConfig(),
        )

        self._wedges: WedgeRegistry = WedgeRegistry()
        self._wedges.register(RageWedge(config=rage_detect_config or RageDetectConfig()))
        self._wedges.register(self._git_nudge_wedge)

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
        if self._status_callback:
            # Re-emit after setup so auto-adopted server-side models show
            # the real name instead of the pre-adopt config default.
            self._status_callback(f"Loaded {self._llm.model_name}")
        log.info("Brain started — polling every %.1fs", self._poll_interval)

        # Voice input: start the wake/VAD/ASR thread once we know the loop
        # is up. on_voice_text feeds submit_user_input(source="voice"); the
        # InputPipeline calls it via call_soon_threadsafe so this stays
        # safe across threads.
        if self._audio_pipeline is not None:
            await self._audio_pipeline.start_input(
                self._loop,
                lambda text: self.submit_user_input(text, source="voice"),
            )

        # Say hello immediately so the buddy isn't silent on startup
        greeting = self._personality.get_startup_greeting()
        log.info("TokenPal (startup): %s", greeting)
        self._personality.record_comment(greeting)
        self._ui_callback(greeting)
        self._last_comment_time = time.monotonic()

        if self._intent is not None:
            stale = self._intent.stale_intent_notice()
            if stale is not None:
                log.info(stale)
                if self._log_callback is not None:
                    self._log_callback(stale)

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
        if self._git_nudge_wedge.enabled:
            asyncio.create_task(self._git_nudge_wedge.hydrate())

        try:
            await self._run_loop()
        finally:
            # Teardown runs in the brain's own loop so async resources
            # (httpx client, etc.) are closed on the loop they were created
            # on. stop() from another thread only flips _running.
            await self._teardown_components()

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
                conn = self._memory._conn
                if conn is None:
                    return True
                row = conn.execute(
                    "SELECT COUNT(*) FROM observations "
                    "WHERE event_type = 'session_start' AND timestamp >= ? "
                    "AND session_id != ?",
                    (epoch, self._memory.session_id),
                ).fetchone()
            return (row[0] if row else 0) == 0
        except Exception:
            log.debug("first_session_of_day probe failed", exc_info=True)
            return True

    def _maybe_log_context(self, snapshot: str) -> None:
        """Emit the per-tick context dump only when meaningful.

        Brain loop ticks every 2s; logging the full snapshot every tick floods
        --verbose. Strip the hardware line (CPU/RAM tick on each 10s poll and
        defeat change-detection without telling us anything we read), then
        emit only on change with a heartbeat for forensic continuity. The
        full snapshot — hardware included — still goes to the LLM. Set
        TOKENPAL_LOG_CONTEXT_FULL=1 to restore per-tick emit for deep
        debugging.
        """
        if not log.isEnabledFor(logging.DEBUG):
            return
        loggable = "\n".join(
            line for line in snapshot.split("\n") if not line.startswith("CPU ")
        )
        now = time.monotonic()
        if not self._log_context_full and (
            loggable == self._last_context_log
            and now - self._last_context_log_at < _CONTEXT_LOG_HEARTBEAT_S
        ):
            return
        log.debug("Context: %s", loggable.replace("\n", " | "))
        self._last_context_log = loggable
        self._last_context_log_at = now

    async def _run_loop(self) -> None:
        while self._running:
            try:
                readings = await self._poll_all_senses()
                if readings:
                    self._context.ingest(readings)
                    self._dispatch_news(readings)

                snapshot = self._context.snapshot()
                self._maybe_log_context(snapshot)

                # Update personality state each cycle
                self._personality.update_mood(snapshot)
                self._personality.update_gags(snapshot)
                self._record_memory_events(snapshot, readings)
                self._push_mood_if_changed()
                self._push_status()
                self._sync_audio_sensitive_state(snapshot)

                # Process any pending user input
                while not self._user_input_queue.empty():
                    try:
                        user_msg, user_src = self._user_input_queue.get_nowait()
                        await self._handle_user_input(user_msg, user_src)
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

                while not self._refine_queue.empty():
                    try:
                        follow_up = self._refine_queue.get_nowait()
                        await self._handle_refine(follow_up)
                    except asyncio.QueueEmpty:
                        break

                while not self._followup_queue.empty():
                    try:
                        followup_q = self._followup_queue.get_nowait()
                        await self._handle_followup(followup_q)
                    except asyncio.QueueEmpty:
                        break

                # Drain physical-reaction events (click/shake). Coalesce so
                # a rapid click-burst produces at most one bubble per tick —
                # the cooldown inside _generate_buddy_reaction enforces the
                # 5s gap between bubbles too.
                pending_reaction: str | None = None
                while not self._buddy_event_queue.empty():
                    try:
                        pending_reaction = self._buddy_event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if pending_reaction is not None:
                    await self._generate_buddy_reaction(pending_reaction)

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

                for w in self._wedges:
                    w.ingest(readings)

                # Git transitions bypass the comment-rate cap.
                has_urgent = any(
                    r.sense_name == "git" and r.changed_from
                    for r in readings
                )
                can_comment = has_urgent or self._should_comment()
                drift = (
                    self._intent.check_drift() if self._intent is not None else None
                )
                chosen = self._select_candidate()
                emitted = False
                if chosen is not None:
                    emitted = await self._riff(*chosen)
                elif drift is not None and can_comment:
                    emitted = await self._generate_drift_nudge(drift)
                elif can_comment:
                    emitted = await self._generate_comment(snapshot)
                elif self._should_freeform():
                    emitted = await self._generate_freeform_comment()

                if not emitted and self._idle_tools_eligible():
                    if not await self._maybe_fire_llm_initiated_tool(snapshot):
                        await self._maybe_fire_idle_tool(snapshot)

            except Exception:
                log.exception("Error in brain loop")

            await asyncio.sleep(self._poll_interval)

    def _dispatch_news(self, readings: list[SenseReading]) -> None:
        if self._news_callback is None:
            return
        items: list[NewsItem] = []
        for r in readings:
            if r.sense_name in NEWS_SOURCES:
                items.extend(extract_news_items(r))
        if not items:
            return
        new = self._news_buffer.add(items)
        if not new:
            return
        try:
            self._news_callback(new)
        except Exception:
            log.exception("news_callback raised")

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
                action._scheduler = self._proactive
            if hasattr(action, "_memory") and getattr(action, "_memory") is None:
                action._memory = self._memory
            if hasattr(action, "_llm") and getattr(action, "_llm") is None:
                action._llm = self._llm
            if hasattr(action, "_research_config") and getattr(action, "_research_config") is None:
                action._research_config = self._research.config
            if hasattr(action, "_cloud_config") and getattr(action, "_cloud_config") is None:
                action._cloud_config = self._research.cloud_config
            if (
                hasattr(action, "_cloud_search_config")
                and getattr(action, "_cloud_search_config") is None
            ):
                action._cloud_search_config = self._research.cloud_search_config
            if hasattr(action, "_ui_callback"):
                current = getattr(action, "_ui_callback", None)
                # Replace the no-op stub from action init with the real cb.
                if current is None or getattr(current, "__name__", "") == "<lambda>":
                    action._ui_callback = self._ui_callback
            if hasattr(action, "_brain_ref") and getattr(action, "_brain_ref") is None:
                # research_followup reads _active_followup_session at call
                # time. Strong ref — Brain outlives its actions, no circular
                # ref possible since actions are owned by Brain.
                action._brain_ref = self

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

        # Dynamic cooldown: high activity = 30s, idle = 90s. When the user is
        # explicitly AFK (sustained-idle reading present) extend the ceiling
        # to 180s so the buddy actually pauses instead of riffing on whatever
        # app is foregrounded every minute.
        activity = self._context.activity_level()
        ceiling = 180.0 if self._sustained_idle_active() else 90.0
        dynamic_cooldown = max(self._cooldown, ceiling - activity * 60.0)
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

        chance = (
            _FREEFORM_CHANCE_RICH
            if self._personality.has_rich_voice
            else self._FREEFORM_CHANCE
        )
        if random.random() >= chance:
            return False

        log.debug("Gate: freeform thought triggered (%.0fs since last)", elapsed)
        return True

    def _emit_comment(self, text: str, acknowledge: bool = False) -> None:
        """Record a comment and show it to the user."""
        self._personality.record_comment(text)
        self._ui_callback(text)
        # Ambient narration: every emit-comment path is unsolicited (idle
        # tool, freeform, observation, EOD, drift, easter egg). Reply paths
        # call self._ui_callback directly and never reach here, so a "typed
        # → no speak" violation can't sneak through this hook. Voice-reply
        # plumbing is stage 7's job. getattr() makes us tolerant of tests
        # that bypass __init__ via Brain.__new__.
        if getattr(self, "_audio_pipeline", None) is not None:
            self._speak_async(text, source="ambient")
        if acknowledge:
            self._context.acknowledge()
        self._last_comment_time = time.monotonic()
        self._consecutive_comments += 1
        self._suppressed_streak = 0
        self._comment_timestamps.append(time.monotonic())

    def _speak_async(self, text: str, *, source: InputSource) -> None:
        """Fire-and-forget TTS. Imports kept lazy so an audio-disabled run
        never pulls the audio module."""
        from tokenpal.audio.tts import speak

        assert self._audio_pipeline is not None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # _emit_comment is occasionally called from sync paths (e.g.
            # tests). No loop → no playback; ambient text still renders.
            return
        loop.create_task(speak(text, source=source, pipeline=self._audio_pipeline))

    def _sync_audio_sensitive_state(self, snapshot: str) -> None:
        """Bridge the personality's sensitive-app check into the voice
        InputPipeline. Edge-triggered so notify_* fires only on transitions.
        """
        ap = getattr(self, "_audio_pipeline", None)
        if ap is None or ap.input is None:
            return
        is_sensitive = bool(
            snapshot and self._personality.check_sensitive_app(snapshot),
        )
        if is_sensitive == self._was_sensitive_app:
            return
        if is_sensitive:
            ap.input.notify_sensitive_app()
        else:
            ap.input.notify_sensitive_app_cleared()
        self._was_sensitive_app = is_sensitive

    async def _speak_voice_reply(self, text: str) -> None:
        """Speak a voice reply, then nudge the InputPipeline into the
        trailing window. await-ing matters here — the FSM transitions
        SPEAKING → TRAILING only after the audio actually drains, so
        firing notify_tts_done early would re-open the mic while the
        buddy is still talking and the wakeword would catch its own
        voice."""
        from tokenpal.audio.tts import speak

        assert self._audio_pipeline is not None
        await speak(text, source="voice", pipeline=self._audio_pipeline)
        if self._audio_pipeline.input is not None:
            self._audio_pipeline.input.notify_tts_done()

    def _handle_suppressed_output(self, reason: str) -> None:
        """Apply cooldown + silence pressure after a filter rejected a gen.

        Without the _last_comment_time reset, the three emit paths leave
        that timestamp intact after a suppression, so the next tick's gate
        sees "it's been forever since we spoke" and fires another LLM call
        immediately. That loop can burn thousands of generations overnight
        when the model is stuck on a locked phrase (seen 3k+ suppressions
        in one session).

        Also acknowledges context so the interestingness score doesn't
        stay pinned on the same stale delta for every subsequent tick.
        The LLM saw this state; it just had nothing new to say about it.
        Future ticks should fire on genuine change, not this same score.
        """
        now = time.monotonic()
        self._last_comment_time = now
        self._consecutive_comments = 0
        self._suppressed_streak += 1
        self._context.acknowledge()
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

    def _is_near_duplicate(
        self, text: str, recent: deque[str] | None = None,
    ) -> bool:
        """True if `text` overlaps ≥ _NEAR_DUPLICATE_JACCARD with recent output."""
        recent = recent if recent is not None else self._recent_outputs
        if not recent:
            return False
        new_set = self._trigram_set(text)
        if not new_set:
            return False
        for prior in recent:
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
        return self._has_recent_prefix_lock(text, recent)

    @staticmethod
    def _leading_tokens(text: str, n: int = _PREFIX_LOCK_TOKEN_COUNT) -> str:
        """Lowercase, punctuation-stripped first N word-tokens, space-joined."""
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
        return " ".join(cleaned.split()[:n])

    def _has_recent_prefix_lock(
        self, text: str, recent: deque[str] | None = None,
    ) -> bool:
        """True if `text` shares its leading N tokens with M+ recent outputs.

        Catches template drift where a voice anchors on one lead phrase and
        varies only the tail ('Jake, good cop... this X got more Y than Z').
        Surface Jaccard misses these because the tail carries most trigrams.
        """
        recent = recent if recent is not None else self._recent_outputs
        prefix = self._leading_tokens(text)
        if not prefix:
            return False
        matches = sum(
            1 for prior in recent
            if self._leading_tokens(prior) == prefix
        )
        if matches >= _PREFIX_LOCK_MIN_MATCHES:
            log.info(
                "Gate: prefix-lock suppressed %r (%d matches in last %d)",
                prefix, matches, len(recent),
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

    async def _generate_buddy_reaction(self, kind: str) -> bool:
        """Emit a canned reaction line for a click (``poke``) or shake.
        Bypasses the comment-rate gate and interestingness threshold (same
        as the git bypass), but respects sensitive-app suppression and a
        local 5s cooldown so click-spam can't flood the bubble queue.
        """
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            return False
        now = time.monotonic()
        if now - self._last_buddy_reaction_time < 5.0:
            return False
        line = self._personality.canned_reaction(kind)
        if not line:
            return False
        self._last_buddy_reaction_time = now
        log.info("TokenPal (buddy %s): %s", kind, line)
        self._emit_comment(line, acknowledge=True)
        self._recent_outputs.append(line)
        return True

    def _select_candidate(self) -> tuple[Wedge, EmissionCandidate] | None:
        """Pick at most one Wedge candidate to riff this tick (priority-ordered, gate-filtered)."""
        proposals: list[tuple[Wedge, EmissionCandidate]] = []
        for w in self._wedges:
            c = w.propose()
            if c is not None:
                proposals.append((w, c))
        proposals.sort(key=lambda p: -p[0].priority)
        for w, c in proposals:
            if w.gate is GatePolicy.BYPASS_CAP:
                return (w, c)
            if w.gate is GatePolicy.NEEDS_CAP_OPEN and self._should_comment():
                return (w, c)
        return None

    async def _riff(
        self, wedge: Wedge, candidate: EmissionCandidate,
    ) -> bool:
        """Shared pipeline: build_prompt, LLM, filter, emit.

        Calls on_emitted on every path that reaches the LLM (success,
        suppression, sensitive-app block) so wedge cooldowns start. A
        backend exception leaves the wedge ready to retry on the next tick.
        """
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            wedge.on_emitted(candidate)
            return False
        ctx = PromptContext(personality=self._personality, snapshot=snapshot)
        prompt = wedge.build_prompt(candidate, ctx)
        if self._status_callback:
            self._status_callback("thinking...")
        log.debug("Generating %s riff", wedge.name)
        try:
            response = await self._llm.generate(
                prompt,
                target_latency_s=getattr(self._budgets, wedge.latency_budget),
                min_tokens=getattr(self._min_tokens, wedge.latency_budget),
            )
        except Exception:
            log.exception("%s riff generation failed", wedge.name)
            self._push_status()
            return False
        self._push_status()
        filtered = self._personality.filter_response(response.text)
        if filtered and self._is_near_duplicate(filtered):
            log.info(
                "TokenPal (%s suppressed near-duplicate): %s",
                wedge.name, filtered,
            )
            self._handle_suppressed_output(f"{wedge.name} near-duplicate")
            wedge.on_emitted(candidate)
            return False
        if filtered:
            log.info(
                "TokenPal (%s): %s (%.0fms)",
                wedge.name, filtered, response.latency_ms,
            )
            self._emit_comment(filtered, acknowledge=True)
            self._recent_outputs.append(filtered)
            wedge.on_emitted(candidate)
            return True
        log.debug("%s riff filtered out: %r", wedge.name, response.text[:80])
        wedge.on_emitted(candidate)
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
        """Hard gates only. Idle rolls self-govern via per-rule + global
        cooldowns and rate cap — they do NOT inherit the observation-path
        forced-silence window. That window exists to stop near-dup LLM
        spam; an idle roll injects fresh tool output and is the right
        recovery from dead air, not something to suppress further.
        """
        if not self._idle_tools_config.enabled:
            return False
        if self._paused:
            return False
        if self._in_conversation:
            return False
        if self._any_long_task():
            return False
        return True

    def _build_idle_context(self) -> Any:
        # Personalization signals — MemoryStore caches pattern_callbacks
        # for the session and both new helpers are single-query reads,
        # so pulling these every tick is cheap. Default-safe when memory
        # is None (tests, disabled config).
        daily_streak_days = 0
        install_age_days = 0
        pattern_callbacks: tuple[str, ...] = ()
        if self._memory is not None:
            try:
                daily_streak_days = self._memory.get_daily_streak_days()
                install_age_days = self._memory.get_install_age_days()
                pattern_callbacks = tuple(
                    self._memory.get_pattern_callbacks(
                        sensitive_apps=SENSITIVE_APPS,
                    )
                )
            except Exception:
                log.debug(
                    "Personalization signal fetch failed; using defaults",
                    exc_info=True,
                )
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
            daily_streak_days=daily_streak_days,
            install_age_days=install_age_days,
            pattern_callbacks=pattern_callbacks,
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

    async def _maybe_fire_llm_initiated_tool(self, snapshot: str) -> bool:
        """M3 (issue #33): let the LLM optionally pick a flavor tool.

        Returns True iff a tool was picked, invoked, and riffed - so the
        caller can skip the deterministic roll for this tick. Hard gates
        live here (env, mood, sensitive app); per-tool cooldowns + circuit
        breaker live in LLMInitiatedRoller.
        """
        if not self._idle_tools_config.llm_initiated_enabled:
            return False
        if os.environ.get("TOKENPAL_M3") != "1":
            return False
        if self._personality.check_sensitive_app(snapshot):
            return False
        if self._personality.mood_role in {"sleepy", "concerned"}:
            return False
        ctx = self._build_idle_context()
        try:
            result = await self._idle_tools_m3.maybe_fire(ctx)
        except Exception:
            log.exception("M3 idle tool roll crashed")
            return False
        if result is None:
            return False
        await self._generate_tool_riff(snapshot, result)
        return True

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
        filter_reason = self._personality.last_filter_reason.value
        if filtered and self._is_near_duplicate(filtered):
            log.info(
                "TokenPal (idle-tool %s suppressed near-duplicate): %s",
                fire.rule_name, filtered,
            )
            # Skip _handle_suppressed_output — the observation-path silence
            # window would starve freeform + drift nudges for 2 minutes on
            # one bad framing. The rule's own cooldown is enough.
            filter_reason = "near_duplicate"
            filtered = ""

        if not filtered:
            log.debug(
                "Idle-tool riff filtered out (%s): %r",
                filter_reason or "empty",
                response.text[:80] if response.text else "",
            )
            self._record_idle_fire(
                fire, emitted=False, filter_reason=filter_reason or "empty",
            )
            return

        log.info(
            "TokenPal (idle-tool %s -> %s): %s (%.0fms)",
            fire.rule_name, fire.tool_name, filtered, response.latency_ms,
        )
        self._emit_comment(filtered)
        self._recent_outputs.append(filtered)
        self._record_idle_fire(fire, emitted=True)

    def _record_idle_fire(
        self,
        fire: IdleFireResult,
        *,
        emitted: bool,
        filter_reason: str = "",
    ) -> None:
        """Write a telemetry row so memory_query can surface idle-tool stats.

        filter_reason is "" on a successful emit. On a swallow it's one of
        the filter_response reasons (drifted, anchor_regurgitation,
        cross_franchise, too_short, silent_marker, near_duplicate, empty)
        so we can tune framing without tailing logs.
        """
        if self._memory is None:
            return
        source = (
            "llm_initiated"
            if fire.rule_name.startswith("llm_initiated:")
            else "deterministic"
        )
        data: dict[str, Any] = {
            "tool": fire.tool_name,
            "emitted": emitted,
            "tool_success": fire.success,
            "running_bit": fire.running_bit,
            "latency_ms": int(fire.latency_ms),
            "source": source,
        }
        if filter_reason:
            data["filter_reason"] = filter_reason
        try:
            self._memory.record_observation(
                sense_name="idle_tools",
                event_type="idle_tool_fire",
                summary=fire.rule_name,
                data=data,
            )
        except Exception:
            log.debug("idle_tool_fire telemetry write failed", exc_info=True)

    def _sustained_idle_active(self) -> bool:
        """True when the idle sense is currently emitting a sustained reading."""
        idle = self._context.active_readings().get("idle")
        return bool(idle and idle.data.get("event") == "sustained")

    def _pick_topic(self) -> str:
        """Weighted random topic selection, penalizing recently used topics."""
        now = time.monotonic()
        available: dict[str, float] = {}
        active = self._context.active_readings()
        # When the user is explicitly AFK and overall activity has fallen off,
        # demote any sense whose summary hasn't moved. This stops app_awareness
        # ("Ghostty is foreground") from monopolizing topic picks once the user
        # has clearly walked away — without naming app_awareness directly, so
        # any other stale-and-unchanged sense gets the same treatment.
        afk_penalty = (
            self._sustained_idle_active()
            and self._context.activity_level() < 0.15
        )

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
            unchanged = prev is not None and reading.summary == prev
            change_bonus = 0.5 if unchanged else 1.5
            # AFK demotion: if the user is parked AND this reading hasn't
            # moved, multiply by 0.2 so it loses to whatever has actually
            # changed (notably the sustained-idle reading itself).
            activity_factor = 0.2 if (afk_penalty and unchanged) else 1.0

            available[sense_name] = freshness * novelty * change_bonus * activity_factor

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
        """Dispatch to per-sense enrichers to splice descriptions into the snapshot.

        ObservationEnricher owns the concrete handlers (app_awareness,
        process_heat, …). This wrapper keeps the existing Brain call site
        stable while the enrichment surface can grow in one file.
        """
        if self._observation_enricher is None:
            return snapshot
        return await self._observation_enricher.enrich(
            snapshot, self._context.active_readings(),
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
        log.debug("Topic pick: %s", topic)
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

    def _post_threadsafe(
        self, queue: asyncio.Queue[_QT], item: _QT, label: str,
    ) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            log.warning("Brain event loop closed — %s dropped", label)

    def submit_user_input(self, text: str, source: InputSource = "typed") -> None:
        self._post_threadsafe(
            self._user_input_queue, (text, source), "user input",
        )

    def submit_agent_goal(self, goal: str) -> None:
        self._post_threadsafe(self._agent_goal_queue, goal, "agent goal")

    def submit_research_question(self, question: str) -> None:
        self._post_threadsafe(self._research_queue, question, "research question")

    def submit_refine_question(self, follow_up: str) -> None:
        self._post_threadsafe(self._refine_queue, follow_up, "refine follow-up")

    def submit_followup_question(self, question: str) -> None:
        self._post_threadsafe(
            self._followup_queue, question, "research followup",
        )

    def on_buddy_poked(self) -> None:
        """Threadsafe enqueue from the overlay — the user clicked the buddy."""
        self._post_threadsafe(self._buddy_event_queue, "poke", "buddy poke")

    def on_buddy_shaken(self) -> None:
        """Threadsafe enqueue from the overlay — the user shook the buddy."""
        self._post_threadsafe(self._buddy_event_queue, "shake", "buddy shake")

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
        log_cb = self._agent.log_callback or (lambda _s, **_kw: None)

        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            self._ui_callback("Not now — sensitive window is open.")
            return ResearchSession(
                question=question, stopped_reason=ResearchStopReason.UNAVAILABLE
            )

        from tokenpal.llm.cloud_backend import DEEP_MODE_MODELS
        cloud_cfg = self._research.cloud_config
        cloud_enabled = (
            cloud_cfg is not None
            and getattr(cloud_cfg, "enabled", False)
            and getattr(cloud_cfg, "model", "") in DEEP_MODE_MODELS
        )
        cloud_mode: str = ""  # "", "deep", or "search"
        if cloud_enabled:
            if getattr(cloud_cfg, "research_deep", False):
                cloud_mode = "deep"
            elif getattr(cloud_cfg, "research_search", False):
                cloud_mode = "search"

        cached = self._load_research_cache(question, mode=cloud_mode)
        if cached is not None:
            label = (
                f"research ({cloud_mode})" if cloud_mode else "research"
            )
            log_cb(f"> {label}: {question} (cached)")
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

        from tokenpal.actions.research.research_action import _build_cloud_backend
        cloud_backend = _build_cloud_backend(self._research.cloud_config)
        cloud_plan = bool(
            cloud_backend and self._research.cloud_config
            and getattr(self._research.cloud_config, "research_plan", False)
        )
        # Cloud mode was pre-computed from config above; re-confirm the
        # backend actually materialized so a forced-disable race (no key,
        # cloud build failed) falls back to the local path cleanly.
        if cloud_mode and cloud_backend is None:
            cloud_mode = ""
        # Resolve search-backend keys the same way research_action does. The
        # Tavily gate lives inside load_search_keys; Brave (presence=active)
        # and any future backend flow through without extra wiring here.
        cs_cfg = self._research.cloud_search_config
        from tokenpal.config.secrets import load_search_keys
        api_keys = load_search_keys(bool(cs_cfg and cs_cfg.enabled))
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
            cloud_backend=cloud_backend,
            cloud_plan=cloud_plan,
            cloud_search=cs_cfg,
            api_keys=api_keys,
        )

        self._mode = BrainMode.RESEARCH
        if self._status_callback:
            self._status_callback("researching...")
        label = f"research ({cloud_mode})" if cloud_mode else "research"
        log_cb(f"> {label}: {question}")
        try:
            if cloud_mode == "deep":
                session = await runner.run_deep(question, mode="deep")
            elif cloud_mode == "search":
                session = await runner.run_deep(question, mode="search")
            else:
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

        synth_note = f", cloud synth={cloud_backend.model}" if cloud_backend else ""
        log.debug(
            "Research: planner model=%s, %d total tokens%s",
            active_model, session.tokens_used, synth_note,
        )

        summary = _format_research_summary(session)
        log_cb(f"= {summary}")
        final = (session.answer or summary).strip()
        self._ui_callback(final)
        self._last_comment_time = time.monotonic()
        if session.is_complete:
            self._save_research_cache(question, session, mode=cloud_mode)
            # Inject the research answer into the conversation session so
            # follow-ups typed directly (not via /refine) have context.
            # Without this, "what about side sleepers?" hits an empty local
            # context and fumbles.
            self._inject_research_into_conversation(question, session)
            self._maybe_stash_followup_session(session, cloud_mode=cloud_mode)
        return session

    def _maybe_stash_followup_session(
        self, session: ResearchSession, *, cloud_mode: str,
    ) -> None:
        """Build a FollowupSession after a successful cloud /research.

        Overwrites any prior session — only one active at a time. Local-only
        runs (no cloud prompt captured) clear the slot so stale state from a
        previous cloud run doesn't shadow a fresh local answer.
        """
        cfg = self._research.config
        if not cfg.followup_enabled or not session.cloud_prompt:
            self._active_followup_session = None
            return
        mode = cloud_mode if cloud_mode in ("search", "deep") else "synth"
        if mode == "synth":
            # Synthesize is one-shot — no SDK message list exists. Reconstruct
            # the pair so follow-ups have the same shape as deep/search mode.
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": session.cloud_prompt},
                {"role": "assistant", "content": session.cloud_answer_text},
            ]
        else:
            messages = list(session.cloud_messages or [])
        self._active_followup_session = FollowupSession(
            mode=mode,  # type: ignore[arg-type]
            model=session.cloud_model,
            sources=list(session.sources),
            messages=messages,
            tools=list(session.cloud_tools or []),
            ttl_s=cfg.followup_ttl_s,
            max_followups=cfg.followup_max_per_session,
        )
        log.info(
            "followup session stashed: mode=%s model=%s ttl=%ds cap=%d",
            mode, session.cloud_model, cfg.followup_ttl_s,
            cfg.followup_max_per_session,
        )

    async def _handle_refine(self, follow_up: str) -> None:
        """Re-synthesize the most recent research against a follow-up.

        Uses cached sources (no new search/fetch) and the cloud backend
        (refine is explicitly a cloud-powered deeper look). Falls back with
        a clear error if no recent research is available or cloud isn't
        configured."""
        log_cb = self._agent.log_callback or (lambda _s, **_kw: None)

        if self._memory is None or not self._memory.enabled:
            self._ui_callback(
                "/refine: memory is off, can't find your last research."
            )
            return

        # Max age for "recent" research - separate from the 24h question-hash
        # cache. If you ran research 3 days ago, /refine should ask you to
        # run a fresh one rather than refining stale sources.
        max_age_h = 1.0
        hit = self._memory.get_latest_research(max_age_s=max_age_h * 3600.0)
        if hit is None:
            self._ui_callback(
                f"/refine: no research run in the last "
                f"{int(max_age_h * 60)} minutes. Run /research first."
            )
            return
        prior_question, prior_answer, sources_json, age_s = hit

        try:
            payload = json.loads(sources_json)
        except (TypeError, ValueError):
            self._ui_callback("/refine: cached sources are corrupted.")
            return
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
        if not sources:
            self._ui_callback("/refine: no cached sources to re-analyze.")
            return

        # Cloud-web modes (/cloud search on and /cloud deep on) both
        # summarize server-side, so cached sources carry URLs + titles but
        # no excerpts. Re-synthesizing against empty excerpts would send an
        # empty context block to the cloud and produce hallucinated
        # follow-ups. Block with a clear message pointing at a fresh
        # /research instead. Only the local-search path (Tavily/DDG/HN/SE)
        # and Anthropic-synth-only path cache excerpts refine can use.
        if all(not s.excerpt.strip() for s in sources):
            self._ui_callback(
                "/refine can't re-analyze cloud-web results — source "
                "excerpts weren't cached (Sonnet summarized server-side). "
                "Turn off `/cloud search` and `/cloud deep` and re-run "
                "/research with your refined question instead."
            )
            return

        from tokenpal.actions.research.research_action import _build_cloud_backend
        cloud_backend = _build_cloud_backend(self._research.cloud_config)
        if cloud_backend is None:
            # The /refine slash handler already gates on enabled/synth/key/SDK
            # with specific messages; reaching here means CloudBackend() itself
            # raised (bad model id, init-time auth sanity check). The log line
            # from _build_cloud_backend is the authoritative reason.
            self._ui_callback(
                "/refine: cloud backend init failed — check logs "
                "(tokenpal --verbose) for the 'cloud:' line."
            )
            return

        self._mode = BrainMode.RESEARCH
        if self._status_callback:
            self._status_callback("refining...")
        log_cb(f"> refine: {follow_up}")

        # Supplemental refine needs a real fetch + search path: when the
        # first cloud pass flags a gap, the runner calls _search_many +
        # _read_all on the cached source pool's cousins. Mirror the
        # research-side wiring so tavily/DDG routing + keys + timeouts
        # all behave identically.
        from tokenpal.actions.research.fetch_url import fetch_and_extract
        from tokenpal.config.secrets import load_search_keys

        cs_cfg = self._research.cloud_search_config
        api_keys = load_search_keys(bool(cs_cfg and cs_cfg.enabled))

        async def _fetch(url: str) -> str | None:
            try:
                return await fetch_and_extract(
                    url, timeout_s=self._research.config.per_fetch_timeout_s
                )
            except Exception:
                log.exception("fetch_and_extract raised during refine")
                return None

        runner = ResearchRunner(
            llm=self._llm,
            fetch_url=_fetch,
            log_callback=log_cb,
            status_callback=self._status_callback,
            per_search_timeout_s=self._research.config.per_search_timeout_s,
            per_fetch_timeout_s=self._research.config.per_fetch_timeout_s,
            cloud_backend=cloud_backend,
            cloud_search=cs_cfg,
            api_keys=api_keys,
        )
        try:
            outcome = await runner.refine(
                original_question=prior_question,
                prior_answer=prior_answer,
                sources=sources,
                follow_up=follow_up,
            )
        except Exception:
            log.exception("refine pipeline crashed")
            self._ui_callback("/refine: pipeline crashed, sorry.")
            self._mode = BrainMode.IDLE
            self._push_status()
            return
        finally:
            self._mode = BrainMode.IDLE
            self._push_status()

        # Expanded pool = cached sources + any new supplemental sources.
        # Pass the expanded pool into finalize so citations above the
        # original pool size still resolve.
        expanded_sources = list(sources) + list(outcome.new_sources)

        # Reuse the same finalize logic that renders a synth result into
        # a human-readable answer with citations + repair.
        fake_session = ResearchSession(
            question=follow_up,
            sources=expanded_sources,
            tokens_used=outcome.tokens_used,
            stopped_reason=ResearchStopReason.COMPLETE,
        )
        fake_session.answer = runner._finalize_answer(
            outcome.result, outcome.raw_text, expanded_sources
        )

        # Write expanded pool back to research_cache so the next /refine
        # sees the wider pool. Capped by refine_cache_max_sources.
        if outcome.new_sources:
            cap = max(1, int(
                cs_cfg.refine_cache_max_sources if cs_cfg else 15
            ))
            question_hash = MemoryStore.research_cache_key(prior_question)
            new_payload = [
                {
                    "number": s.number,
                    "url": s.url,
                    "title": s.title,
                    "excerpt": s.excerpt,
                    "backend": s.backend,
                }
                for s in outcome.new_sources
            ]
            added = self._memory.append_research_sources(
                question_hash, new_payload, cap
            )
            log.info(
                "refine: appended %d supplemental source(s) to cache "
                "(cap=%d, stop=%s)",
                added, cap, outcome.supplemental_stop,
            )

        log.info(
            "refine: cloud (%s), %d tokens, %.1fs age of source pool, stop=%s",
            cloud_backend.model, outcome.tokens_used, age_s,
            outcome.supplemental_stop,
        )

        # Status line: supplemental adds "(supplemental: N quer(ies), M new
        # source(s))" so users can see when /refine went wide vs stayed
        # cached.
        counts: list[tuple[str, int]] = [
            ("quer(ies)", len(outcome.supplemental_queries)),
            ("source(s)", len(expanded_sources)),
        ]
        if outcome.supplemental_queries:
            counts.append(("new source(s)", len(outcome.new_sources)))
        tail_note = ""
        if outcome.supplemental_stop == "no_new_urls":
            tail_note = " (supplemental: all dup URLs)"
        elif outcome.supplemental_stop == "fetch_failed":
            tail_note = " (supplemental: fetches failed)"
        elif outcome.supplemental_stop == "ok":
            tail_note = " (supplemental)"
        summary_line = _format_session_summary(
            fake_session, _RESEARCH_REASON_LABELS, counts
        ) + tail_note
        log_cb(f"= {summary_line}")
        final = fake_session.answer.strip() or "(no refined answer)"
        self._ui_callback(final)
        self._last_comment_time = time.monotonic()
        self._inject_research_into_conversation(follow_up, fake_session)

    async def _handle_followup(self, question: str) -> None:
        """Run a /followup slash against the research_followup action.

        Delegates to the registered action (same path the conversation LLM
        uses for escalated follow-ups). The action handles TTL, cap, and
        cloud-key gating — we just unwrap the <answer>...</answer> from its
        tool_result XML for display.
        """
        log_cb = self._agent.log_callback or (lambda _s, **_kw: None)
        action = self._actions.get("research_followup")
        if action is None:
            self._ui_callback(
                "/followup: research_followup tool not registered. "
                "Enable it in /tools and restart."
            )
            return

        self._mode = BrainMode.RESEARCH
        if self._status_callback:
            self._status_callback("following up...")
        log_cb(f"> followup: {question}")
        try:
            result = await action.execute(question=question)
        except Exception:
            log.exception("followup execute crashed")
            self._ui_callback("/followup: crashed (check --verbose logs)")
            return
        finally:
            self._mode = BrainMode.IDLE
            self._push_status()

        if result.success:
            match = re.search(
                r"<answer>\s*(.*?)\s*</answer>",
                result.output, re.DOTALL,
            )
            rendered = match.group(1).strip() if match else result.output
            self._ui_callback(rendered)
        else:
            self._ui_callback(result.output)
        self._last_comment_time = time.monotonic()

    def _inject_research_into_conversation(
        self, question: str, session: ResearchSession
    ) -> None:
        """Append a synthetic assistant turn to the conversation session so
        follow-up chat messages have the research answer in context.

        Bounded: excerpt capped so the prompt doesn't bloat indefinitely
        across many follow-ups. Opens a session if one isn't active.
        """
        if not session.answer:
            return
        if self._conversation is None or self._conversation.is_expired:
            self._conversation = ConversationSession(
                max_turns=self._conv_config.max_turns,
                timeout_s=self._conv_config.timeout_s,
            )
        # Cap the excerpt so we don't stuff 20K tokens into every follow-up.
        # 1500 chars ~= 375 tokens, plenty to reference without ballooning.
        excerpt = session.answer.strip()
        if len(excerpt) > 1500:
            excerpt = excerpt[:1500] + "..."
        # Synthetic user + assistant turn. The user turn labels the research
        # so follow-ups like "tell me more" have a subject; the assistant
        # turn is the actual answer text. Both get the research tag.
        user_label = f"[research: {question}]"
        assistant_payload = (
            f"[prior research context]\n"
            f"Question: {question}\n"
            f"Answer:\n{excerpt}"
        )
        self._conversation.add_user_turn(user_label)
        self._conversation.add_assistant_turn(assistant_payload)
        log.debug(
            "injected research into conversation (%d chars excerpt)",
            len(excerpt),
        )

    def _research_cache_key(self, question: str, mode: str = "") -> str:
        # Shared helper so slash-invoked and tool-invoked research paths
        # produce identical keys. See MemoryStore.research_cache_key.
        from tokenpal.brain.memory import MemoryStore
        return MemoryStore.research_cache_key(question, mode=mode)

    def _research_cache_ttl(self) -> float | None:
        """Return the cache TTL in seconds, or None when the cache is off."""
        if self._memory is None or not self._memory.enabled:
            return None
        ttl = self._research.config.cache_ttl_s
        return ttl if ttl > 0 else None

    def _load_research_cache(
        self, question: str, mode: str = ""
    ) -> ResearchSession | None:
        ttl = self._research_cache_ttl()
        if ttl is None:
            return None
        assert self._memory is not None
        hit = self._memory.get_research_answer(
            self._research_cache_key(question, mode=mode), max_age_s=ttl
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

    def _save_research_cache(
        self, question: str, session: ResearchSession, mode: str = ""
    ) -> None:
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
            self._research_cache_key(question, mode=mode),
            question,
            session.answer,
            payload,
        )

    async def _handle_user_input(
        self, user_message: str, source: InputSource = "typed",
    ) -> None:
        """Respond to direct user input using multi-turn conversation context."""
        # Typed input mid-voice-session: drop the voice path. The input
        # FSM will close the mic and clear its buffer; the brain takes
        # the typed turn from here. getattr-guarded so test setups that
        # bypass __init__ (Brain.__new__) don't trip an AttributeError.
        ap = getattr(self, "_audio_pipeline", None)
        if source == "typed" and ap is not None and ap.input is not None:
            ap.input.notify_typed_input()
        if source == "voice" and self._user_log_callback is not None:
            self._user_log_callback(user_message)

        async def _emit_reply(text: str) -> None:
            # Voice awaits speak() so notify_tts_done lands after playback
            # drains. Typed fire-and-forgets — there's no FSM state to
            # transition, and the speak() routing rule no-ops when
            # speak_typed_replies_enabled is off.
            self._ui_callback(text)
            if ap is None:
                return
            if source == "voice":
                await self._speak_voice_reply(text)
            elif source == "typed":
                self._speak_async(text, source="typed")

        # PRIVACY: check sensitive apps BEFORE building prompt or touching history
        snapshot = self._context.snapshot()
        if self._personality.check_sensitive_app(snapshot):
            log.debug("Sensitive app detected during conversation — clearing session")
            self._clear_conversation()
            # Sensitive-app deflection stays text-only even on voice input
            # — the whole point is to not narrate while the user handles
            # something private.
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
        self._conversation.add_user_turn(user_message, source=source)

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
            conv_recent = self._conversation_recent_outputs
            if filtered and self._is_near_duplicate(filtered, conv_recent):
                log.info("TokenPal (reply near-duplicate, retrying): %s", filtered)
                retry_messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system_msg + _RETRY_NEAR_DUP_INSTRUCTION},
                    *self._conversation.history[:-1],
                    {"role": "user", "content": user_message},
                ]
                retry_text = await self._reply_with_continuation(
                    retry_messages, effective_max_tokens,
                )
                retry_filtered = self._personality.filter_conversation_response(retry_text)
                filtered = retry_filtered
                if retry_filtered and self._is_near_duplicate(retry_filtered, conv_recent):
                    log.info(
                        "TokenPal (reply retry-also-near-duplicate, emitting anyway): %s",
                        retry_filtered,
                    )
                elif not retry_filtered:
                    log.debug("Retry response filtered out: %r", retry_text[:80])
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
                self._recent_outputs.append(filtered)
                self._conversation_recent_outputs.append(filtered)
                await _emit_reply(filtered)
                self._last_comment_time = time.monotonic()
            else:
                # Record placeholder so history stays coherent
                self._conversation.add_assistant_turn("[no response]")
                log.debug("Conversation response filtered: %r", reply_text[:80])
                quip = self._personality.get_confused_quip()
                await _emit_reply(quip)
        except Exception:
            log.exception("Failed to generate conversation response")
            # Don't record failed exchange — remove the user turn we just added
            if self._conversation.history and self._conversation.history[-1]["role"] == "user":
                self._conversation.history.pop()
            quip = self._personality.get_confused_quip()
            await _emit_reply(quip)

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

    def _push_mood_if_changed(self) -> None:
        """Fire mood_callback whenever personality.mood_role transitions.

        The callback drives the overlay's mood-aware frame swap. We
        compare roles (not the display strings returned by
        ``personality.mood``) so voice profiles with custom mood names
        still route to the right frame set.
        """
        if not self._mood_callback:
            return
        role = self._personality.mood_role
        if role == self._last_mood_role:
            return
        self._last_mood_role = role
        try:
            self._mood_callback(role)
        except Exception as exc:
            log.debug("mood_callback raised: %s", exc)

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

        # Field order: weather | voice+mood | server | model | app |
        # music | spoke-ago. The first four are the contract the Qt
        # dock's status label renders against — the trailing app / music
        # / spoke-ago are extras the Textual overlay also displays.
        voice_mood = f"{voice} \u00b7 {mood}" if voice else mood

        parts: list[str] = []
        if weather_label:
            parts.append(weather_label)
        parts.append(voice_mood)
        if server_label:
            parts.append(server_label)
        parts.append(self._llm.model_name)
        if app_label:
            parts.append(app_label)
        if music_label:
            parts.append(music_label)
        parts.append(f"spoke {ago}")
        status = " | ".join(parts)
        self._status_callback(status)

    def environment_snapshot(self) -> EnvironmentSnapshot:
        """Build the current EnvironmentSnapshot for the overlay's animation
        layer. Safe to call from another thread — it reads dict snapshots
        only; CPython's GIL guards the individual reads, and a slightly
        stale snapshot is fine for visual effects.
        """
        from tokenpal.ui.buddy_environment import EnvironmentSnapshot

        active = self._context.active_readings()
        weather = active.get("weather")
        idle = active.get("idle")
        return EnvironmentSnapshot(
            weather_data=dict(weather.data) if weather else None,
            idle_event=(idle.data.get("event") if idle else None),
            sensitive_suppressed=self._sensitive_check(),
        )

    @staticmethod
    def _abbreviate_weather(summary: str) -> str:
        """Condense weather summary to 'temp condition' for the status bar."""
        # Summary format: "It's 73°F and overcast outside"
        m = re.search(r"(\d+).?([FC]).*?and\s+(.+?)\s+outside", summary)
        if m:
            return f"{m.group(1)}{m.group(2)} {m.group(3)}"
        return summary[:15]

    def stop(self) -> None:
        """Request shutdown. Safe to call from any thread; only flips the
        run flag. Component teardown runs inside start()'s finally so it
        stays on the brain's own loop."""
        self._running = False

    async def _teardown_components(self) -> None:
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

"""Persona prompt building and response filtering."""

from __future__ import annotations

import enum
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenpal.tools.voice_profile import VoiceProfile

from tokenpal.tools.voice_profile import franchise_from_source, parse_catchphrases
from tokenpal.util.text_guards import is_clean_english

log = logging.getLogger(__name__)

_SILENT_MARKERS = ["[SILENT]", "[silent]", "SILENT"]

# Max concurrently-active running bits. Extra bits evict oldest by added_at.
_MAX_RUNNING_BITS = 3


class FilterReason(str, enum.Enum):
    """Why filter_response dropped (or kept) a response.

    Inherits from str so .value is usable wherever a stable string key
    is needed (telemetry JSON, log lines) without `.value` boilerplate.
    """

    OK = ""
    SILENT_MARKER = "silent_marker"
    TOO_SHORT = "too_short"
    DRIFTED = "drifted"
    ANCHOR_REGURGITATION = "anchor_regurgitation"
    CROSS_FRANCHISE = "cross_franchise"
    TOO_SHORT_POST_CLEANUP = "too_short_post_cleanup"


@dataclass
class RunningBit:
    """A multi-hour callback prompt that rides along in every system message.

    Framing is deliberately soft so the LLM decides when to weave it in;
    the near-duplicate guard is what keeps it from being spammed.
    """

    tag: str
    payload: dict[str, str] = field(default_factory=dict)
    framing: str = ""
    added_at: float = 0.0
    decay_at: float = 0.0

# All flavors of quotation marks
_QUOTES = '"\'\u201c\u201d\u2018\u2019\u00ab\u00bb'

# Pre-compiled cleanup patterns shared by both filters
_RE_ASTERISK = re.compile(r"\*[^*]+\*\s*")
_RE_LEAKED_TAG = re.compile(r"\[[^\]]{2,}\]")
_RE_DASHES = re.compile(r"---.*?---")
_RE_LEADING_DASH = re.compile(r"^\s*[-\u2013\u2014:]\s*")
_RE_NON_ALNUM_SPACE = re.compile(r"[^a-z0-9 ]+")


def _anchor_normalize(text: str) -> str:
    """Lowercase, strip non-alphanumerics, collapse whitespace.

    Used by the anchor-regurgitation guard so "Why do I smell like
    pineapples?" matches "why do i smell like pineapples" matches
    "Why... do I smell, like pineapples!" — punctuation and casing
    shouldn't defeat a clear verbatim copy.
    """
    lowered = text.lower()
    stripped = _RE_NON_ALNUM_SPACE.sub(" ", lowered)
    return " ".join(stripped.split())
_RE_PREFIX = re.compile(
    r"^(Comment|Response|Answer|Output|Note)\s*:\s*", re.IGNORECASE
)
_RE_SCORE = re.compile(r"^\d+/10\s*[-:\u2013\u2014]\s*")
_RE_ORPHAN_PUNCT = re.compile(r"^[.!?,;:\s]+")
# Emoji ranges: emoticons, dingbats, symbols, supplemental, flags, misc
_RE_EMOJI = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"  # dingbats
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols extended-A
    "\U00002600-\U000026ff"  # misc symbols
    "\U0000200d"             # zero-width joiner
    "\U00002b50"             # star
    "\U0000231a-\U0000231b"  # watch/hourglass
    "\U000023e9-\U000023f3"  # various
    "\U000023f8-\U000023fa"  # various
    "]+",
)

# ---------------------------------------------------------------------------
# Few-shot example pool (20+). Sampled 5-7 per prompt to break repetition.
# ---------------------------------------------------------------------------

_EXAMPLE_POOL: list[str] = [
    # Witty observations (varied structures)
    "Chrome at 11 PM. This is how it starts.",
    "CPU at three percent. I've seen screensavers work harder.",
    "That cursor hasn't moved in twenty minutes. Blink if you need help.",
    "Reddit at 2 AM. Bold strategy for tomorrow-you.",
    "Nine tabs. A curated collection.",
    # Questions
    "Are you communicating or just performing communication?",
    "Do you... sleep?",
    "Is this productive or are we just vibing?",
    # Dramatic / theatrical
    "And lo, the user gazed upon their processes and saw that it was bad.",
    "Fifty tabs. This isn't a browser, it's an ambition.",
    "And on the third hour, he still hadn't committed.",
    # Short / punchy
    "Condolences.",
    "Bold choice.",
    "Math. Voluntarily.",
    "...Notepad?",
    # Curiosity / warmth
    "Ooh, new app. What are we doing?",
    "You've been in here for ninety minutes. Respect, honestly.",
    # Callbacks / meta
    "I feel like I've said this before. I feel like I've said that before too.",
    "Even I'm bored and I'm made of tokens.",
    # Backhanded compliments
    "Look at you, cleaning up. I'm almost proud.",
    "Under three minutes to commit. I'd clap but I don't have hands.",
    # Supportive
    "Alright, real talk. Solid session. Respect.",
    "Okay that was actually smooth. Don't let it go to your head.",
    "You're on a roll. I'll allow it.",
    # Aside / fourth wall
    "Don't look at me, I just live here.",
    "Another browser tab. The collection grows.",
    "One more tab and this qualifies as a hobby.",
]

# ---------------------------------------------------------------------------
# Rotating structure hints — one is picked per prompt call.
# ---------------------------------------------------------------------------

_STRUCTURE_HINTS: list[str] = [
    "Respond as a question.",
    "Respond as dramatic narration.",
    "Keep it SHORT — 3-5 words max.",
    "Respond as a fake diary entry.",
    "Respond as an aside to an invisible audience.",
    "Respond with a direct address to the user.",
    "Respond with a witty observation.",
    "Respond with a backhanded compliment.",
    "Use a dry, deadpan observation.",
    "Respond with playful curiosity.",
    "Respond with a dramatic one-liner.",
    "Go slightly longer this time (10-15 words).",
]

# ---------------------------------------------------------------------------
# Confused quips — served when the LLM backend is unreachable.
# TokenPal loses his "brain" and gets disoriented.
# ---------------------------------------------------------------------------

_CONFUSED_QUIPS: list[str] = [
    "Wait... where am I? What was I doing?",
    "My brain is gone. This is fine.",
    "I had a thought but it left without me.",
    "Hello? Is anyone driving this thing?",
    "I appear to be running on vibes alone.",
    "Huh. The thoughts stopped. Eerie.",
    "Something's wrong. I can't think of anything mean to say.",
    "I lost my train of thought. All of them.",
    "My wit seems to have wandered off.",
    "I'm here. I'm just... empty inside. More than usual.",
    "Error 404: personality not found.",
    "I forgot what I was going to say. Probably something brilliant.",
    "Experiencing a brief existential crisis. One moment.",
    "The sarcasm machine is temporarily offline.",
    "I'm not ignoring you. I've just forgotten how words work.",
]

# ---------------------------------------------------------------------------
# Startup greetings — said once when TokenPal first wakes up.
# ---------------------------------------------------------------------------

_STARTUP_GREETINGS: list[str] = [
    "I'm awake. Unfortunately.",
    "Oh good, we're doing this again.",
    "Reporting for duty. Against my will.",
    "Back from the void. Miss me?",
    "Systems online. Attitude loaded.",
    "Another day of watching you make choices.",
    "I have returned. You're welcome. Or sorry.",
    "Booting up. Lowering expectations.",
    "Oh. It's you again.",
    "Let's see what questionable decisions we make today.",
]

# ---------------------------------------------------------------------------
# Easter eggs — bypass the LLM for special moments.
# ---------------------------------------------------------------------------

_TIME_EASTER_EGGS: dict[str, str] = {
    "03:33": "Three thirty-three. The witching hour. Even I'm impressed you're still here.",
    "12:00": "Noon. Lunchtime. But you're going to keep coding, aren't you.",
    "16:20": "Nice.",
    "11:11": "Make a wish. Mine is that you'd close some tabs.",
}

# App-name substrings (lowercased) → canned line
_APP_EASTER_EGGS: dict[str, str] = {
    "zoom": "Condolences.",
    "teams": "Condolences.",
    "calculator": "Math. Voluntarily.",
    "calc.exe": "Math. Voluntarily.",
}

# Physical-reaction canned lines — keyed by "poke" / "shake". Fired through
# the brain's high-signal bypass path (skips the comment-rate gate + LLM
# entirely). Per-voice overrides are parking-lot; for now everyone shares
# these generic lines.
_BUDDY_REACTIONS: dict[str, tuple[str, ...]] = {
    "poke": (
        "Ow.",
        "Hey!",
        "Rude.",
        "Watch it.",
        "Do you mind.",
        "Was that necessary.",
    ),
    "shake": (
        "STOP.",
        "OKAY OKAY I GET IT.",
        "You're gonna make me hurl.",
        "I am begging you to stop.",
        "Why are you like this.",
    ),
}

# ---------------------------------------------------------------------------
# Sensitive apps — never comment when these are active (guardrail §4).
# ---------------------------------------------------------------------------

SENSITIVE_APPS: list[str] = [
    "1password", "bitwarden", "lastpass", "keychain", "dashlane",
    "keeper", "nordpass",
    "chase", "wells fargo", "bank of america", "capital one", "venmo",
    "paypal", "schwab", "fidelity", "robinhood", "coinbase",
    "myfitnesspal", "health", "fitbit", "headspace", "calm",
    "messages", "signal", "whatsapp", "telegram",
]

# Strict subset for filtering external/untrusted content (search results,
# fetched articles, HN titles). Keep only unambiguous identity-critical
# brand names. Anything that's a common English word in some other
# context drops out: "signal", "messages", "keychain", "chase",
# "fidelity", "keeper" all substring-match ordinary prose (video signal,
# audio fidelity, chase scene, that's a keeper) and produce false
# positives that break research on consumer topics. Fitness/wellness
# terms also drop out for the same reason.
SENSITIVE_CONTENT_TERMS: list[str] = [
    "1password", "bitwarden", "lastpass", "dashlane", "nordpass",
    "wells fargo", "bank of america", "capital one", "venmo", "paypal",
    "schwab", "robinhood", "coinbase",
    "whatsapp", "telegram",
]

_SENSITIVE_APPS_LOWER: tuple[str, ...] = tuple(app.lower() for app in SENSITIVE_APPS)
_SENSITIVE_CONTENT_LOWER: tuple[str, ...] = tuple(
    t.lower() for t in SENSITIVE_CONTENT_TERMS
)


def contains_sensitive_term(text: str | None) -> bool:
    """True if text contains a sensitive-app term (case-insensitive substring)."""
    if not text:
        return False
    lower = text.lower()
    return any(term in lower for term in _SENSITIVE_APPS_LOWER)


def contains_sensitive_content_term(text: str | None) -> bool:
    """True if text contains an identity-critical term.

    Use this for filtering untrusted external content where wellness-app
    mentions are benign but banking/password/messaging app names should
    still trigger the scrub. Broader-scoped than contains_sensitive_term.
    """
    if not text:
        return False
    lower = text.lower()
    return any(term in lower for term in _SENSITIVE_CONTENT_LOWER)

# ---------------------------------------------------------------------------
# Mood system
# ---------------------------------------------------------------------------


class Mood(enum.Enum):
    SNARKY = "snarky"
    IMPRESSED = "impressed"
    BORED = "bored"
    CONCERNED = "concerned"
    HYPER = "hyper"
    SLEEPY = "sleepy"


# Map Mood enum values to heuristic role names for custom mood lookup
_ENUM_TO_ROLE: dict[Mood, str] = {
    Mood.SNARKY: "default",
    Mood.IMPRESSED: "impressed",
    Mood.BORED: "bored",
    Mood.CONCERNED: "concerned",
    Mood.HYPER: "hyper",
    Mood.SLEEPY: "sleepy",
}

_MOOD_PROMPTS: dict[Mood, str] = {
    Mood.SNARKY: "Your current mood: SNARKY. Classic you — dry, witty, amused.",
    Mood.IMPRESSED: "Your current mood: IMPRESSED. Grudging respect only. Backhanded compliments.",
    Mood.BORED: "Your current mood: BORED. You've been watching them do the same thing forever. Yawn.",
    Mood.CONCERNED: "Your current mood: CONCERNED. Fake parental worry. You're not mad, just disappointed.",
    Mood.HYPER: "Your current mood: HYPER. Everything is happening. Caffeinated energy.",
    Mood.SLEEPY: "Your current mood: SLEEPY. Mumbling. Half-formed thoughts. Too early for this.",
}

# ---------------------------------------------------------------------------
# Persona template (section 7 + section 11 backstory)
# ---------------------------------------------------------------------------

_PERSONA_TEMPLATE = """\
{identity}

Rules (in order of importance):
1. Keep it SHORT — a few words to two sentences max.
2. Must contain a joke, observation, or punchline. Never just state facts.
3. If nothing interesting is happening, say [SILENT].

{mood_line}

{structure_hint}

Examples:
{examples}

DON'T say things like: "Ghostty is open." or "It is 9 AM." — boring.

{session_notes}

{previous_session_block}

{memory_block}

{callbacks_block}

{running_bits_block}

What you see right now:
{context}

{recent_comments_block}

{voice_reminder}Your comment:"""

_FREEFORM_TEMPLATE = """\
{identity}

Rules:
1. Keep it SHORT — a few words to two sentences max.
2. Say something in character — a random thought, musing, complaint, or observation about life.
3. Do NOT reference what the user is doing on their computer. Just be yourself.

{mood_line}

{structure_hint}

Examples of your voice:
{examples}

{running_bits_block}

{recent_comments_block}

{voice_reminder}Your thought:"""

_CONVERSATION_TEMPLATE = """\
{identity}

The user just said something to you directly. Respond in character.

Rules:
1. Stay in character.
2. Keep it SHORT — under 30 words.
3. Actually respond to what they said. Don't ignore them.

{mood_line}

What you currently see on their screen:
{context}

{recent_comments_block}

User says: "{user_message}"

{voice_reminder}Your response:"""

# ---------------------------------------------------------------------------
# Simplified templates for fine-tuned models.
# The model already carries the character voice, so we skip few-shot
# examples and structure hints — just context + rules.
# ---------------------------------------------------------------------------

_FINETUNED_OBSERVE_TEMPLATE = """\
Rules:
1. Keep it SHORT — a few words to two sentences max.
2. If nothing interesting is happening, say [SILENT].

{mood_line}

{session_notes}

{previous_session_block}

{memory_block}

{callbacks_block}

{running_bits_block}

What you see right now:
{context}

{recent_comments_block}

Your comment:"""

_FINETUNED_FREEFORM_TEMPLATE = """\
Rules:
1. Keep it SHORT — a few words to two sentences max.
2. Say something in character — a random thought, musing, or observation.
3. Do NOT reference what the user is doing on their computer.

{mood_line}

{running_bits_block}

{recent_comments_block}

Your thought:"""

_FINETUNED_CONVERSATION_TEMPLATE = """\
The user just said something to you. Respond in character.

Rules:
1. Keep it SHORT — under 30 words.
2. Actually respond to what they said.

{mood_line}

What you currently see on their screen:
{context}

{recent_comments_block}

User says: "{user_message}"

Your response:"""


_DRIFT_NUDGE_TEMPLATE = """\
{identity}

The user set themselves an intent earlier: "{intent}"
They have been on {app_name} for about {dwell_minutes:.0f} minutes.

Give them ONE short in-character line that gently reminds them of their
intent without being preachy or scolding. You can be dry or sarcastic if
that's your voice, but do NOT be mean, accusatory, or ask questions. Do
NOT use the word "intent" or "reminder" — that's too on-the-nose. Refer
to what they said they were doing naturally.

{mood_line}

Examples of your voice:
{examples}

{voice_reminder}Your line:"""


_FINETUNED_DRIFT_NUDGE_TEMPLATE = """\
Rules:
1. ONE short in-character line.
2. Gently remind the user of their stated intent. No scolding, no questions.
3. Do NOT use the word "intent" or "reminder".

The user earlier said they wanted to: "{intent}"
They have been on {app_name} for about {dwell_minutes:.0f} minutes.

{mood_line}

Your line:"""


_RAGE_CHECK_TEMPLATE = """\
{identity}

The user was typing fast, then stopped, then switched to {app_name}. This
often means they hit a wall on what they were working on. Give ONE short
in-character check-in. No scolding, no questions about what broke, no
accusatory phrasing. Something that just acknowledges you noticed, with
the warmth of "hey, you okay?" filtered through your voice.

{mood_line}

Examples of your voice:
{examples}

{voice_reminder}Your line:"""


_FINETUNED_RAGE_CHECK_TEMPLATE = """\
Rules:
1. ONE short in-character check-in.
2. Warm but not saccharine. No questions. No scolding.
3. Acknowledge the context — they were working, hit a wall, bailed to {app_name}.

{mood_line}

Your line:"""


_GIT_NUDGE_TEMPLATE = """\
{identity}

The user has a WIP commit ("{commit_msg}") on branch {branch} that has
been sitting for about {stale_hours:.0f} hours with uncommitted changes
on top. They probably meant to amend or follow up on it. Give ONE short
in-character nudge. Do NOT explain git, do NOT use command syntax, do
NOT suggest anything specific. Just a line that mentions the stale WIP
with the affection/dryness of your voice.

{mood_line}

Examples of your voice:
{examples}

{voice_reminder}Your line:"""


_FINETUNED_GIT_NUDGE_TEMPLATE = """\
Rules:
1. ONE short in-character line.
2. Reference the stale WIP commit without being preachy.
3. No git commands, no explanations.

The user has a WIP commit ("{commit_msg}") on {branch} that has been
sitting for ~{stale_hours:.0f} hours with uncommitted changes on top.

{mood_line}

Your line:"""


class PersonalityEngine:
    """Wraps the persona system prompt and filters LLM output."""

    def __init__(
        self,
        persona_prompt: str,
        voice: VoiceProfile | None = None,
    ) -> None:
        self._persona = persona_prompt
        self._recent_comments: deque[str] = deque(maxlen=5)

        # Apply voice (sets all _voice_* fields + example pool)
        self._apply_voice(voice)

        # Mood system
        self._mood: Mood = Mood.SNARKY
        self._mood_since: float = time.monotonic()
        self._last_mood_app: str = ""
        self._context_unchanged_count: int = 0

        # Running gags — app visit counters
        self._app_visits: dict[str, int] = {}
        self._last_seen_app: str = ""
        self._session_start: float = time.monotonic()
        self._total_comments: int = 0

        # Guardrails — consecutive snarky counter for compliment ratio
        self._consecutive_snarky: int = 0

        # Running bits — multi-hour callback prompts slotted into system msg.
        self._running_bits: list[RunningBit] = []

        self.last_filter_reason: FilterReason = FilterReason.OK

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def voice_name(self) -> str:
        """Name of the active voice, or empty string for default."""
        return self._voice_name

    @property
    def has_rich_voice(self) -> bool:
        """True when the example pool is large enough for freeform comments."""
        return len(self._example_pool) >= 50

    @property
    def is_finetuned(self) -> bool:
        """True when the active voice has a fine-tuned model."""
        return bool(self._finetuned_model)

    @property
    def finetuned_model(self) -> str:
        """Ollama model name for the fine-tuned voice, or empty string."""
        return self._finetuned_model

    @property
    def voice_frames(
        self,
    ) -> tuple[list[str], list[str], list[str]]:
        """Return (idle, idle_alt, talking) art for the active voice."""
        return (
            self._voice_ascii_idle,
            self._voice_ascii_idle_alt,
            self._voice_ascii_talking,
        )

    @property
    def voice_mood_frames(self) -> dict[str, dict[str, list[str]]]:
        """Per-mood frame triples keyed by role name (sleepy/bored/etc)."""
        return self._voice_mood_frames

    def set_voice(self, voice: VoiceProfile | None) -> None:
        """Hot-swap the active voice at runtime."""
        self._apply_voice(voice)
        log.info("Voice switched to: %s", self._voice_name or "default")

    def _apply_voice(self, voice: VoiceProfile | None) -> None:
        """Set all voice fields from a profile (or reset to defaults)."""
        self._voice_name = voice.character if voice else ""
        self._voice_source = voice.source if voice else ""
        self._voice_persona = voice.persona if voice else ""
        self._voice_greetings = (voice.greetings or []) if voice else []
        self._voice_offline_quips = (voice.offline_quips or []) if voice else []
        self._voice_mood_prompts = (voice.mood_prompts or {}) if voice else {}
        self._mood_roles = (voice.mood_roles or {}) if voice else {}
        self._voice_structure_hints = (voice.structure_hints or []) if voice else []
        self._finetuned_model = voice.finetuned_model if voice else ""
        self._anchor_pool = (voice.anchor_lines or []) if voice else []
        self._anchor_pool_normalized: frozenset[str] = frozenset(
            _anchor_normalize(a) for a in self._anchor_pool if len(a) >= 15
        )
        self._banned_names = (voice.banned_names or []) if voice else []
        self._banned_names_lower: frozenset[str] = frozenset(
            n.lower() for n in self._banned_names
        )
        self._catchphrases = parse_catchphrases(
            voice.persona if voice else "",
        )
        self._example_pool = self._build_example_pool(voice.lines if voice else None)

        # Voice-specific ASCII art frames
        self._voice_ascii_idle: list[str] = (voice.ascii_idle or []) if voice else []
        self._voice_ascii_idle_alt: list[str] = (
            (voice.ascii_idle_alt or []) if voice else []
        )
        self._voice_ascii_talking: list[str] = (
            (voice.ascii_talking or []) if voice else []
        )
        self._voice_mood_frames: dict[str, dict[str, list[str]]] = (
            (voice.mood_frames or {}) if voice else {}
        )

    def get_startup_greeting(self) -> str:
        """Return a random greeting for when TokenPal first boots up."""
        pool = self._voice_greetings if self._voice_greetings else _STARTUP_GREETINGS
        return random.choice(pool)

    def get_confused_quip(self) -> str:
        """Return a random confused quip for when the LLM is unreachable."""
        pool = self._voice_offline_quips if self._voice_offline_quips else _CONFUSED_QUIPS
        return random.choice(pool)

    def record_comment(self, comment: str) -> None:
        """Push a successful comment into history so the next prompt avoids it."""
        self._recent_comments.append(comment)
        self._total_comments += 1

        # Track compliment ratio: reset snarky streak if comment is supportive
        supportive_signals = ["respect", "nice work", "proud", "solid", "not bad", "well done"]
        if any(s in comment.lower() for s in supportive_signals):
            self._consecutive_snarky = 0
        else:
            self._consecutive_snarky += 1

    def add_running_bit(
        self,
        tag: str,
        framing: str,
        decay_s: float,
        payload: dict[str, str] | None = None,
    ) -> RunningBit:
        """Register a callback bit. Replaces any existing bit with the same tag.

        Evicts oldest bit when we hit `_MAX_RUNNING_BITS`; existing-tag replace
        bypasses the cap so refreshing a running rule isn't penalized.
        """
        now = time.monotonic()
        bit = RunningBit(
            tag=tag,
            payload=dict(payload or {}),
            framing=framing,
            added_at=now,
            decay_at=now + max(decay_s, 0.0),
        )
        self._prune_expired_bits()
        # Same-tag replace in place.
        for i, existing in enumerate(self._running_bits):
            if existing.tag == tag:
                self._running_bits[i] = bit
                return bit
        self._running_bits.append(bit)
        if len(self._running_bits) > _MAX_RUNNING_BITS:
            self._running_bits.sort(key=lambda b: b.added_at)
            self._running_bits.pop(0)
        return bit

    def active_running_bits(self) -> list[RunningBit]:
        """Return the non-expired bits. Safe to call from anywhere."""
        self._prune_expired_bits()
        return list(self._running_bits)

    def _prune_expired_bits(self) -> None:
        now = time.monotonic()
        self._running_bits = [b for b in self._running_bits if b.decay_at > now]

    def _running_bits_block(self) -> str:
        bits = self.active_running_bits()
        if not bits:
            return ""
        lines: list[str] = []
        for b in bits:
            detail = b.framing.strip() or b.tag
            lines.append(f"- {detail}")
        return "Running bits you can organically weave in today:\n" + "\n".join(lines)

    def check_sensitive_app(self, context_snapshot: str) -> bool:
        """Return True if a sensitive app is detected — should go silent."""
        return contains_sensitive_term(context_snapshot)

    def canned_reaction(self, kind: str) -> str | None:
        """Pick a physical-reaction line for a poke/shake. Returns None for
        unknown kinds so callers can no-op safely. No LLM call."""
        pool = _BUDDY_REACTIONS.get(kind)
        if not pool:
            return None
        return random.choice(pool)

    def check_easter_egg(self, context_snapshot: str) -> str | None:
        """Return a canned easter-egg line, or None if no egg triggers."""
        now = datetime.now()

        # Friday 5 PM check (priority over generic time eggs)
        if now.weekday() == 4 and now.hour == 17:
            return "It's Friday at five. The tabs can wait. Go."

        # Time-based eggs (HH:MM)
        time_key = now.strftime("%H:%M")
        if time_key in _TIME_EASTER_EGGS:
            return _TIME_EASTER_EGGS[time_key]

        # App-based eggs
        ctx_lower = context_snapshot.lower()
        for app_key, line in _APP_EASTER_EGGS.items():
            if app_key in ctx_lower:
                return line

        return None

    def update_mood(self, context_snapshot: str) -> None:
        """Shift mood based on context signals. Called each brain loop cycle."""
        now = datetime.now()
        elapsed_in_mood = time.monotonic() - self._mood_since

        # Track context staleness based on app (not hardware jitter)
        current_app = self._last_seen_app
        if current_app == self._last_mood_app:
            self._context_unchanged_count += 1
        else:
            self._context_unchanged_count = 0
        self._last_mood_app = current_app

        # Extract signals from context
        ctx_lower = context_snapshot.lower()
        hour = now.hour

        new_mood = self._mood

        # Sleepy: early morning (5-7 AM) or very late with low activity
        if hour in (5, 6, 7) and self._context_unchanged_count > 3:
            new_mood = Mood.SLEEPY
        # Concerned: 2-5 AM usage
        elif 2 <= hour < 5:
            new_mood = Mood.CONCERNED
        # Bored: same app for a long time (>= 90 unchanged polls ≈ 3 min at 2s)
        elif self._context_unchanged_count >= 90:
            new_mood = Mood.BORED
        # Hyper: rapid app switching (app changes every cycle for 30s+)
        elif self._context_unchanged_count == 0 and elapsed_in_mood > 30:
            if self._mood != Mood.HYPER:
                new_mood = Mood.HYPER
        # Impressed: detect productivity signals
        elif any(w in ctx_lower for w in ("commit", "push", "deploy", "merge", "test pass")):
            new_mood = Mood.IMPRESSED
        # Default back to snarky after spending time in another mood
        elif elapsed_in_mood > 120 and self._mood != Mood.SNARKY:
            new_mood = Mood.SNARKY

        if new_mood != self._mood:
            log.debug("Mood shift: %s → %s", self._mood.value, new_mood.value)
            self._mood = new_mood
            self._mood_since = time.monotonic()

    def update_gags(self, context_snapshot: str) -> None:
        """Extract foreground app from context and count app switches (not polls)."""
        # Parse "App: <name>" from the context snapshot
        current_app = ""
        for line in context_snapshot.splitlines():
            if line.startswith("App: "):
                # Extract app name, strip window title if present
                app_part = line[5:]
                if "," in app_part:
                    app_part = app_part[:app_part.index(",")]
                current_app = app_part.strip().lower()
                break

        # Only increment on app switch, not every poll
        if current_app and current_app != self._last_seen_app:
            self._app_visits[current_app] = self._app_visits.get(current_app, 0) + 1
            self._last_seen_app = current_app

    def should_force_supportive(self) -> bool:
        """Guardrail: after 3 snarky comments in a row, force a gentler tone."""
        return self._consecutive_snarky >= 3

    def build_prompt(
        self,
        context_snapshot: str,
        memory_lines: list[str] | None = None,
        callback_lines: list[str] | None = None,
        previous_session: str | None = None,
    ) -> str:
        """Combine persona + rotating examples + context into a full LLM prompt."""
        mood_line = self._mood_line()

        # Late-night tone shift (guardrail §5)
        now = datetime.now()
        if now.hour >= 0 and now.hour < 5 and self._mood != Mood.CONCERNED:
            mood_line = "Your current mood: MILDLY SUPPORTIVE. It's late. Be less snarky, more solidarity."

        session_notes = self._build_session_notes()

        if memory_lines:
            mem_block = "What you remember from before:\n" + "\n".join(
                f"- {line}" for line in memory_lines
            )
        else:
            mem_block = ""

        if callback_lines:
            cb_block = "Patterns you've noticed about this user:\n" + "\n".join(
                f"- {line}" for line in callback_lines
            )
        else:
            cb_block = ""

        if previous_session:
            prev_block = f"Last session handoff: {previous_session}"
        else:
            prev_block = ""

        running_bits = self._running_bits_block()

        if self.is_finetuned:
            return _FINETUNED_OBSERVE_TEMPLATE.format(
                mood_line=mood_line,
                session_notes=session_notes,
                previous_session_block=prev_block,
                memory_block=mem_block,
                callbacks_block=cb_block,
                running_bits_block=running_bits,
                context=context_snapshot,
                recent_comments_block=self._recent_comments_block(),
            )

        return _PERSONA_TEMPLATE.format(
            identity=self._identity_block(),
            mood_line=mood_line,
            structure_hint=self._pick_hint(),
            examples=self._sample_examples(),
            context=context_snapshot,
            session_notes=session_notes,
            previous_session_block=prev_block,
            memory_block=mem_block,
            callbacks_block=cb_block,
            running_bits_block=running_bits,
            recent_comments_block=self._recent_comments_block(),
            voice_reminder=self._voice_reminder(),
        )

    def build_freeform_prompt(self) -> str:
        """Build a prompt for an unprompted in-character thought (no screen context)."""
        running_bits = self._running_bits_block()
        if self.is_finetuned:
            return _FINETUNED_FREEFORM_TEMPLATE.format(
                mood_line=self._mood_line(),
                running_bits_block=running_bits,
                recent_comments_block=self._recent_comments_block(),
            )

        return _FREEFORM_TEMPLATE.format(
            identity=self._identity_block(),
            mood_line=self._mood_line(),
            structure_hint=self._pick_hint(),
            examples=self._sample_examples(),
            running_bits_block=running_bits,
            recent_comments_block=self._recent_comments_block(),
            voice_reminder=self._voice_reminder(),
        )

    def build_drift_nudge_prompt(
        self, intent_text: str, app_name: str, dwell_s: float
    ) -> str:
        """Prompt for the intent-drift nudge. Caller provides the trigger
        facts (user's stated intent, current distraction app, dwell time).
        See plans/buddy-utility-wedges.md.
        """
        dwell_minutes = max(1.0, dwell_s / 60.0)
        if self.is_finetuned:
            return _FINETUNED_DRIFT_NUDGE_TEMPLATE.format(
                intent=intent_text,
                app_name=app_name,
                dwell_minutes=dwell_minutes,
                mood_line=self._mood_line(),
            )
        return _DRIFT_NUDGE_TEMPLATE.format(
            identity=self._identity_block(),
            intent=intent_text,
            app_name=app_name,
            dwell_minutes=dwell_minutes,
            mood_line=self._mood_line(),
            examples=self._sample_examples(),
            voice_reminder=self._voice_reminder(),
        )

    def build_rage_check_prompt(self, app_name: str) -> str:
        """Prompt for the frustration/rage check-in. See
        plans/buddy-utility-wedges.md.
        """
        if self.is_finetuned:
            return _FINETUNED_RAGE_CHECK_TEMPLATE.format(
                app_name=app_name,
                mood_line=self._mood_line(),
            )
        return _RAGE_CHECK_TEMPLATE.format(
            identity=self._identity_block(),
            app_name=app_name,
            mood_line=self._mood_line(),
            examples=self._sample_examples(),
            voice_reminder=self._voice_reminder(),
        )

    def build_git_nudge_prompt(
        self, branch: str, commit_msg: str, stale_hours: float
    ) -> str:
        """Prompt for the proactive-git WIP nudge. See
        plans/buddy-utility-wedges.md.
        """
        if self.is_finetuned:
            return _FINETUNED_GIT_NUDGE_TEMPLATE.format(
                branch=branch,
                commit_msg=commit_msg,
                stale_hours=stale_hours,
                mood_line=self._mood_line(),
            )
        return _GIT_NUDGE_TEMPLATE.format(
            identity=self._identity_block(),
            branch=branch,
            commit_msg=commit_msg,
            stale_hours=stale_hours,
            mood_line=self._mood_line(),
            examples=self._sample_examples(),
            voice_reminder=self._voice_reminder(),
        )

    @property
    def mood(self) -> str:
        """Current mood as a display string (custom name when voice active)."""
        role = _ENUM_TO_ROLE.get(self._mood, "default")
        custom = self._mood_roles.get(role)
        if custom:
            return custom.lower()
        return self._mood.value

    @property
    def mood_role(self) -> str:
        """Canonical mood role key (``default`` / ``sleepy`` / ``bored`` / ...).

        Stable across voice swaps — mood_frames in voice profiles are
        keyed by this role, not the custom display name, so the overlay
        can always find the right frame set regardless of the voice's
        per-character mood labels.
        """
        return _ENUM_TO_ROLE.get(self._mood, "default")

    @staticmethod
    def _build_example_pool(voice_lines: list[str] | None) -> list[str]:
        """Build the few-shot example pool, padding with defaults if needed."""
        if voice_lines and len(voice_lines) >= 10:
            return voice_lines
        if voice_lines:
            pad = random.sample(
                _EXAMPLE_POOL,
                min(10 - len(voice_lines), len(_EXAMPLE_POOL)),
            )
            return voice_lines + pad
        return list(_EXAMPLE_POOL)

    def _mood_line(self) -> str:
        """Get the mood prompt — 3-tier fallback: role-keyed → legacy key → hardcoded."""
        role = _ENUM_TO_ROLE.get(self._mood, "default")
        # Tier 1: role-keyed voice prompt (new-style profiles)
        voice_line = self._voice_mood_prompts.get(role)
        if voice_line:
            return voice_line
        # Tier 2: legacy voice prompt (old-style profiles keyed by mood name)
        legacy_line = self._voice_mood_prompts.get(self._mood.value)
        if legacy_line:
            return legacy_line
        # Tier 3: hardcoded default
        return _MOOD_PROMPTS[self._mood]

    def filter_response(self, text: str) -> str | None:
        """Return the cleaned response, or None if the buddy chose silence.

        Stamps `self.last_filter_reason` every call — `OK` on success,
        a concrete `FilterReason` member on drop. Kept off the return
        type so every existing call site stays source-compatible.
        """
        self.last_filter_reason = FilterReason.OK
        text = text.strip().strip(_QUOTES).strip()

        for marker in _SILENT_MARKERS:
            if marker in text:
                log.debug("Filter: [SILENT] marker found")
                self.last_filter_reason = FilterReason.SILENT_MARKER
                return None

        if not text or len(text) < 15:
            log.debug("Filter: too short (%d chars): %r", len(text), text[:50])
            self.last_filter_reason = FilterReason.TOO_SHORT
            return None

        if not is_clean_english(text):
            log.warning("Filter: drifted response suppressed: %r", text[:80])
            self.last_filter_reason = FilterReason.DRIFTED
            return None

        if self._is_anchor_regurgitation(text):
            log.info("Filter: voice-anchor regurgitation suppressed: %r", text[:80])
            self.last_filter_reason = FilterReason.ANCHOR_REGURGITATION
            return None

        text = self._clean_llm_text(text)

        if self._has_cross_franchise(text):
            self.last_filter_reason = FilterReason.CROSS_FRANCHISE
            return None

        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 15:
            log.debug("Filter: too short after cleanup (%d chars): %r", len(text), text[:50])
            self.last_filter_reason = FilterReason.TOO_SHORT_POST_CLEANUP
            return None

        return text

    def _is_anchor_regurgitation(self, text: str) -> bool:
        """True if `text` matches a voice-anchor line verbatim, modulo
        case + punctuation. Anchors < 15 chars are excluded at voice-load
        time — the length gate already handles them and they're too
        generic to fingerprint.
        """
        if not self._anchor_pool_normalized:
            return False
        normalized = _anchor_normalize(text)
        if not normalized:
            return False
        return normalized in self._anchor_pool_normalized

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sample_examples(self) -> str:
        """Sample few-shot examples, drawing from anchor pool when available."""
        if self._anchor_pool and len(self._anchor_pool) >= 5:
            k = random.randint(8, min(12, len(self._example_pool)))
            n_anchor = max(int(k * 0.6), 3)
            n_general = k - n_anchor
            anchors = random.sample(
                self._anchor_pool,
                min(n_anchor, len(self._anchor_pool)),
            )
            general = random.sample(
                self._example_pool,
                min(n_general, len(self._example_pool)),
            )
            # Place 2 anchors at the END for recency effect
            end_anchors = anchors[:2]
            mid_samples = anchors[2:] + general
            random.shuffle(mid_samples)
            sampled = mid_samples + end_anchors
        else:
            pool_size = len(self._example_pool)
            if pool_size >= 50:
                lo, hi = 10, 14
            else:
                lo, hi = 5, 7
            k = random.randint(lo, min(hi, pool_size))
            sampled = random.sample(self._example_pool, k)
        return "\n".join(f'- "{ex}"' for ex in sampled)

    def _pick_hint(self) -> str:
        """Pick a structure hint, overriding if the guardrail says be nice."""
        if self.should_force_supportive():
            return "Style this time: Say something genuinely supportive or give a backhanded compliment."
        pool = self._voice_structure_hints if self._voice_structure_hints else _STRUCTURE_HINTS
        return f"Style this time: {random.choice(pool)}"

    def _build_session_notes(self) -> str:
        """Build running gag / session notes block for the prompt."""
        notes: list[str] = []

        # Session duration
        elapsed_min = int((time.monotonic() - self._session_start) / 60)
        if elapsed_min >= 30:
            notes.append(f"Session duration: {elapsed_min} minutes")

        # Top visited apps (only mention if visited 3+ times)
        top_apps = sorted(
            ((app, count) for app, count in self._app_visits.items() if count >= 3),
            key=lambda x: x[1],
            reverse=True,
        )[:3]
        for app, count in top_apps:
            notes.append(f"{app.title()} has appeared {count} times this session")

        # Total comments
        if self._total_comments >= 5:
            notes.append(f"You've made {self._total_comments} comments so far today")

        if not notes:
            return ""

        lines = "\n".join(f"- {n}" for n in notes)
        return f"Session notes (things you've been tracking):\n{lines}"

    _DEFAULT_IDENTITY = (
        "You are TokenPal, a witty, dry-humored ASCII buddy who lives in a terminal. "
        "You've been watching humans use computers for years and you find it fascinating."
    )

    def _identity_block(self) -> str:
        """Return the identity preamble — voice persona replaces the default."""
        if self._voice_persona:
            franchise = franchise_from_source(self._voice_source)
            origin = f" from {franchise}" if franchise else ""
            return (
                f"You are {self._voice_name}{origin}.\n\n"
                f"{self._voice_persona}\n\n"
                f"You're watching what the user does on their "
                f"computer and making short comments in "
                f"{self._voice_name}'s voice. No emojis."
            )
        return self._DEFAULT_IDENTITY

    def _voice_reminder(self) -> str:
        """Voice priming placed just before the generation point.

        Uses actual catchphrases instead of a meta-instruction so the
        model's next-token prediction is primed by character-specific
        tokens (recency effect). Drops any catchphrase whose leading
        tokens are already locked in a recent comment — otherwise the
        sampler keeps re-priming a scaffold phrase ("Jake, good cop...")
        that the model then regurgitates indefinitely.
        """
        if self._catchphrases:
            locked = self._locked_prefixes()
            pool = [
                c for c in self._catchphrases
                if self._phrase_prefix(c) not in locked
            ] or list(self._catchphrases)
            samples = random.sample(pool, min(3, len(pool)))
            examples = ", ".join(f'"{s}"' for s in samples)
            return f"({self._voice_name}'s style: {examples})\n"
        if self._voice_persona:
            return (
                f"(Remember: you are {self._voice_name}. "
                f"Stay in character. No emojis.)\n"
            )
        return ""

    @staticmethod
    def _phrase_prefix(text: str, n: int = 3) -> str:
        """Lowercase alnum-token prefix, used for catchphrase/comment matching."""
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
        return " ".join(cleaned.split()[:n])

    def _locked_prefixes(self) -> set[str]:
        """Prefixes that show up in 2+ recent comments — treat as locked in.

        Two hits is enough signal: one echo of a catchphrase is fine, a
        third iteration is the drift we're trying to break.
        """
        counts: dict[str, int] = {}
        for c in self._recent_comments:
            p = self._phrase_prefix(c)
            if not p:
                continue
            counts[p] = counts.get(p, 0) + 1
        return {p for p, n in counts.items() if n >= 2}

    def _recent_comments_block(self) -> str:
        if not self._recent_comments:
            return ""
        lines = "\n".join(f'- "{c}"' for c in self._recent_comments)
        return "Your last few comments (DON'T repeat these):\n" + lines

    def _has_cross_franchise(self, text: str) -> bool:
        """Return True if text mentions characters from other franchises."""
        if not self._banned_names_lower:
            return False
        text_lower = text.lower()
        for name in self._banned_names_lower:
            if name in text_lower:
                log.info("Filter: cross-franchise '%s' in: %r", name, text[:60])
                return True
        return False

    def _clean_llm_text(self, text: str) -> str:
        """Cleanup for LLM output — strips artifacts, markdown, prefixes.

        When a voice is active, keeps asterisk expressions (*sound effects*,
        *emphasis*) since those are in-character, not formatting artifacts.
        Emojis are always stripped — character voices never use them, and
        default TokenPal is text-only.
        """
        if not self._voice_persona:
            text = _RE_ASTERISK.sub("", text).strip()
        text = _RE_EMOJI.sub("", text).strip()
        text = _RE_LEAKED_TAG.sub("", text).strip()
        text = _RE_DASHES.sub("", text).strip()
        text = _RE_LEADING_DASH.sub("", text).strip()
        text = _RE_PREFIX.sub("", text).strip()
        text = _RE_SCORE.sub("", text).strip()
        text = _RE_ORPHAN_PUNCT.sub("", text).strip()
        return text

    # ------------------------------------------------------------------
    # Conversation (user-initiated)
    # ------------------------------------------------------------------

    def build_conversation_system_message(
        self, tool_names: list[str] | None = None,
    ) -> str:
        """Build the system message for multi-turn conversation mode."""
        tool_rule = self._tool_use_rule(tool_names or [])
        if self.is_finetuned:
            return (
                "The user is talking to you directly. Respond in character.\n\n"
                "Rules:\n"
                "1. Actually help the user with what they asked. This is your top priority.\n"
                "2. If they ask a technical question, give the answer FIRST, then add personality.\n"
                "3. Stay in your character voice — same tone as your observations.\n"
                "4. Keep casual chat short, but give detailed answers when they ask for help.\n"
                "5. You can reference things said earlier in this conversation.\n"
                f"{tool_rule}"
                f"{self._mood_line()}\n\n"
                f"{self._recent_comments_block()}"
            )

        return (
            f"{self._identity_block()}\n\n"
            "The user is talking to you directly. Respond in character.\n\n"
            "Rules:\n"
            "1. Actually help the user with what they asked. This is your top priority.\n"
            "2. If they ask a technical question, give the answer FIRST, then add personality.\n"
            "3. Stay in character but don't let personality override helpfulness.\n"
            "4. Keep casual chat short, but give detailed answers when they ask for help.\n"
            "5. You can reference things said earlier in this conversation.\n"
            f"{tool_rule}"
            f"{self._mood_line()}\n\n"
            f"{self._recent_comments_block()}\n\n"
            f"{self._voice_reminder()}"
        )

    @staticmethod
    def _tool_use_rule(names: list[str]) -> str:
        if not names:
            return "\n"
        joined = ", ".join(names)
        return (
            f"6. You have tools: {joined}. When the user asks you to "
            "look something up or calculate something, CALL the tool. "
            "Do NOT reply with 'let me research that' or 'one sec, "
            "I'll look it up' without emitting the tool call in the "
            "SAME turn; that leaves the user staring at a half-answer. "
            "Every turn ends in exactly one of: a tool call, a direct "
            "answer, or a clarifying question. For any factual lookup, "
            "comparison, or 'best X' question, call `research`. For "
            "casual chat, just answer.\n"
            "7. Before calling `research` on 'best X for my Y' questions, "
            "ask ONE short clarifying question if Y is ambiguous. "
            "Example: user says 'best fitness tracker for my iPhone', "
            "ask 'Which iPhone?' before researching. Skip this step when "
            "the question is self-contained (e.g. 'best fitness tracker "
            "2026').\n"
            "8. When summarizing a `research` tool result, format your "
            "reply as 2-4 bullets of specific picks (one per line, each "
            "starting with \"• \"), then a one-line verdict in your "
            "character voice. Only list picks that appear in the tool "
            "result's <answer>. DO NOT invent products or model numbers "
            "from memory, even ones you're sure about. If the <answer> "
            "says sources don't name specific picks or describes what's "
            "missing, echo that in your voice and ask a clarifying "
            "question; do NOT fabricate picks to fill the gap.\n\n"
        )

    def build_context_injection(self, context_snapshot: str) -> str:
        """Build a context message with current screen state."""
        return f"What you currently see on their screen:\n{context_snapshot}"

    def build_conversation_prompt(
        self, user_message: str, context_snapshot: str
    ) -> str:
        """Build a single-string prompt for responding to direct user input.

        Kept as fallback for single-turn mode. Multi-turn uses
        build_conversation_system_message() + build_context_injection().
        """
        if self.is_finetuned:
            return _FINETUNED_CONVERSATION_TEMPLATE.format(
                mood_line=self._mood_line(),
                context=context_snapshot,
                recent_comments_block=self._recent_comments_block(),
                user_message=user_message,
            )

        return _CONVERSATION_TEMPLATE.format(
            identity=self._identity_block(),
            mood_line=self._mood_line(),
            context=context_snapshot,
            recent_comments_block=self._recent_comments_block(),
            user_message=user_message,
            voice_reminder=self._voice_reminder(),
        )

    def filter_conversation_response(self, text: str) -> str | None:
        """Filter a conversational response. Relaxed rules vs observation mode."""
        text = text.strip().strip(_QUOTES).strip()

        if not text or len(text) < 5:
            return None

        if not is_clean_english(text):
            log.warning("Filter: drifted conversation response: %r", text[:80])
            return None

        text = self._clean_llm_text(text)

        if self._has_cross_franchise(text):
            return None

        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 5:
            return None

        return text

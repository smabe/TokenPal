"""Persona prompt building and response filtering."""

from __future__ import annotations

import enum
import logging
import random
import re
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenpal.tools.voice_profile import VoiceProfile

from tokenpal.tools.voice_profile import franchise_from_source, parse_catchphrases

log = logging.getLogger(__name__)

_SILENT_MARKERS = ["[SILENT]", "[silent]", "SILENT"]

# All flavors of quotation marks
_QUOTES = '"\'\u201c\u201d\u2018\u2019\u00ab\u00bb'

# Pre-compiled cleanup patterns shared by both filters
_RE_ASTERISK = re.compile(r"\*[^*]+\*\s*")
_RE_LEAKED_TAG = re.compile(r"\[[^\]]{2,}\]")
_RE_DASHES = re.compile(r"---.*?---")
_RE_LEADING_DASH = re.compile(r"^\s*[-\u2013\u2014:]\s*")
_RE_PREFIX = re.compile(
    r"^(Comment|Response|Answer|Output|Note)\s*:\s*", re.IGNORECASE
)
_RE_SCORE = re.compile(r"^\d+/10\s*[-:\u2013\u2014]\s*")
_RE_ORPHAN_PUNCT = re.compile(r"^[.!?,;:\s]+")
_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
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

# ---------------------------------------------------------------------------
# Sensitive apps — never comment when these are active (guardrail §4).
# ---------------------------------------------------------------------------

_SENSITIVE_APPS: list[str] = [
    "1password", "bitwarden", "lastpass", "keychain", "dashlane",
    "keeper", "nordpass",
    "chase", "wells fargo", "bank of america", "capital one", "venmo",
    "paypal", "schwab", "fidelity", "robinhood", "coinbase",
    "myfitnesspal", "health", "fitbit", "headspace", "calm",
    "messages", "signal", "whatsapp", "telegram",
]

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
1. 1-2 sentences. Keep it short.
2. Must contain a joke, observation, or punchline. Never just state facts.
3. If nothing interesting is happening, say [SILENT].

{mood_line}

{structure_hint}

Examples:
{examples}

DON'T say things like: "Ghostty is open." or "It is 9 AM." — boring.

{session_notes}

{memory_block}

What you see right now:
{context}

{recent_comments_block}

{voice_reminder}Your comment:"""

_FREEFORM_TEMPLATE = """\
{identity}

Rules:
1. 1-2 sentences. Keep it short.
2. Say something in character — a random thought, musing, complaint, or observation about life.
3. Do NOT reference what the user is doing on their computer. Just be yourself.

{mood_line}

{structure_hint}

Examples of your voice:
{examples}

{recent_comments_block}

{voice_reminder}Your thought:"""

_CONVERSATION_TEMPLATE = """\
{identity}

The user just said something to you directly. Respond in character.

Rules:
1. Stay in character.
2. Keep it to 1-2 sentences (under 30 words).
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
1. 1-2 sentences. Keep it short.
2. If nothing interesting is happening, say [SILENT].

{mood_line}

{session_notes}

{memory_block}

What you see right now:
{context}

{recent_comments_block}

Your comment:"""

_FINETUNED_FREEFORM_TEMPLATE = """\
Rules:
1. 1-2 sentences. Keep it short.
2. Say something in character — a random thought, musing, or observation.
3. Do NOT reference what the user is doing on their computer.

{mood_line}

{recent_comments_block}

Your thought:"""

_FINETUNED_CONVERSATION_TEMPLATE = """\
The user just said something to you. Respond in character.

Rules:
1. 1-2 sentences (under 30 words).
2. Actually respond to what they said.

{mood_line}

What you currently see on their screen:
{context}

{recent_comments_block}

User says: "{user_message}"

Your response:"""


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
        self._banned_names = (voice.banned_names or []) if voice else []
        self._banned_names_lower: frozenset[str] = frozenset(
            n.lower() for n in self._banned_names
        )
        self._catchphrases = parse_catchphrases(
            voice.persona if voice else "",
        )
        self._example_pool = self._build_example_pool(voice.lines if voice else None)

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

    def check_sensitive_app(self, context_snapshot: str) -> bool:
        """Return True if a sensitive app is detected — should go silent."""
        ctx_lower = context_snapshot.lower()
        return any(app in ctx_lower for app in _SENSITIVE_APPS)

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
        # Bored: same app for a long time (>= 10 unchanged polls)
        elif self._context_unchanged_count >= 10:
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
        self, context_snapshot: str, memory_lines: list[str] | None = None
    ) -> str:
        """Combine persona + rotating examples + context into a full LLM prompt."""
        # Mood line
        mood_line = self._mood_line()

        # Late-night tone shift (guardrail §5)
        now = datetime.now()
        if now.hour >= 0 and now.hour < 5 and self._mood != Mood.CONCERNED:
            mood_line = "Your current mood: MILDLY SUPPORTIVE. It's late. Be less snarky, more solidarity."

        # Build session notes from running gags
        session_notes = self._build_session_notes()

        # Build memory block from persistent history
        if memory_lines:
            mem_block = "What you remember from before:\n" + "\n".join(
                f"- {line}" for line in memory_lines
            )
        else:
            mem_block = ""

        # Fine-tuned models carry the voice — use a simpler prompt
        if self.is_finetuned:
            return _FINETUNED_OBSERVE_TEMPLATE.format(
                mood_line=mood_line,
                session_notes=session_notes,
                memory_block=mem_block,
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
            memory_block=mem_block,
            recent_comments_block=self._recent_comments_block(),
            voice_reminder=self._voice_reminder(),
        )

    def build_freeform_prompt(self) -> str:
        """Build a prompt for an unprompted in-character thought (no screen context)."""
        if self.is_finetuned:
            return _FINETUNED_FREEFORM_TEMPLATE.format(
                mood_line=self._mood_line(),
                recent_comments_block=self._recent_comments_block(),
            )

        return _FREEFORM_TEMPLATE.format(
            identity=self._identity_block(),
            mood_line=self._mood_line(),
            structure_hint=self._pick_hint(),
            examples=self._sample_examples(),
            recent_comments_block=self._recent_comments_block(),
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
        """Return the cleaned response, or None if the buddy chose silence."""
        text = text.strip().strip(_QUOTES).strip()

        for marker in _SILENT_MARKERS:
            if marker in text:
                log.debug("Filter: [SILENT] marker found")
                return None

        if not text or len(text) < 15:
            log.debug("Filter: too short (%d chars): %r", len(text), text[:50])
            return None

        text = self._clean_llm_text(text)

        if self._has_cross_franchise(text):
            return None

        text = self._cap_sentences(text, max_default=2, max_voice=3)

        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 15:
            log.debug("Filter: too short after cleanup (%d chars): %r", len(text), text[:50])
            return None

        return text

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
        tokens (recency effect).
        """
        if self._catchphrases:
            samples = random.sample(
                self._catchphrases,
                min(3, len(self._catchphrases)),
            )
            examples = ", ".join(f'"{s}"' for s in samples)
            return f"({self._voice_name}'s style: {examples})\n"
        if self._voice_persona:
            return (
                f"(Remember: you are {self._voice_name}. "
                f"Stay in character. No emojis.)\n"
            )
        return ""

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

    def _cap_sentences(self, text: str, max_default: int = 2, max_voice: int = 3) -> str:
        """Truncate to N sentences. Voices get more room for excitable characters.

        Short exclamations (under 10 chars like "Huh?!" or "Tired?!")
        don't count toward the cap — exclamatory characters like Finn
        would otherwise burn through the limit in a few words.
        """
        limit = max_voice if self._voice_persona else max_default
        fragments = _RE_SENTENCE_SPLIT.split(text)
        count = 0
        kept: list[str] = []
        for frag in fragments:
            is_full = len(frag.strip()) >= 10
            if is_full:
                count += 1
            if count > limit:
                break
            kept.append(frag)
        if len(kept) < len(fragments):
            return " ".join(kept)
        return text

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

    def build_conversation_prompt(
        self, user_message: str, context_snapshot: str
    ) -> str:
        """Build a prompt for responding to direct user input."""
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
        """Filter a conversational response — relaxed rules vs observation mode."""
        text = text.strip().strip(_QUOTES).strip()

        if not text or len(text) < 5:
            return None

        text = self._clean_llm_text(text)

        if self._has_cross_franchise(text):
            return None

        text = self._cap_sentences(text, max_default=2, max_voice=4)

        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 5:
            return None

        # Relaxed cap — 150 chars (vs 70 for observations)
        if len(text) > 150:
            text = text[:147] + "..."

        return text

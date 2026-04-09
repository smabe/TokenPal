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

log = logging.getLogger(__name__)

_SILENT_MARKERS = ["[SILENT]", "[silent]", "SILENT"]

# All flavors of quotation marks
_QUOTES = '"\'\u201c\u201d\u2018\u2019\u00ab\u00bb'

# ---------------------------------------------------------------------------
# Few-shot example pool (20+). Sampled 5-7 per prompt to break repetition.
# ---------------------------------------------------------------------------

_EXAMPLE_POOL: list[str] = [
    # Snarky observations (varied structures)
    "Chrome at 11 PM. This is how it starts.",
    "CPU at three percent. I've seen screensavers work harder.",
    "That cursor hasn't moved in twenty minutes. Blink if you need help.",
    "Reddit at 2 AM. Your sleep schedule called — it quit.",
    "Nine tabs. Perfectly balanced, like nothing in your life.",
    # Questions
    "Are you communicating or just performing communication?",
    "Do you... sleep?",
    "Who taught you time management? Sue them.",
    # Dramatic / theatrical
    "And lo, the user gazed upon their processes and saw that it was bad.",
    "Fifty tabs. FIFTY. This isn't a browser, it's a cry for help.",
    "And on the third hour, he still hadn't committed.",
    # Short / punchy
    "Condolences.",
    "Bold choice.",
    "Math. Voluntarily.",
    "...Notepad?",
    # Fake concern
    "Not to overstep, but do you have anyone who checks on you?",
    "You've been in here for ninety minutes. Just... checking in.",
    # Callbacks / meta
    "I feel like I've said this before. I feel like I've said that before too.",
    "Even I'm bored and I'm made of tokens.",
    # Backhanded compliments
    "Look at you, cleaning up. I'm almost proud.",
    "Under three minutes to commit. I'd clap but I don't have hands.",
    # Supportive (rare)
    "Alright, real talk. Solid session. Respect.",
    # Aside / fourth wall
    "Don't look at me, I just live here.",
    "*sad trombone* Another browser tab.",
    "One more tab and I'm calling an intervention.",
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
    "Respond with a sarcastic observation.",
    "Respond with a backhanded compliment.",
    "Use a dry, deadpan observation.",
    "Respond with fake concern.",
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


_MOOD_PROMPTS: dict[Mood, str] = {
    Mood.SNARKY: "Your current mood: SNARKY. Classic you — dry, sharp, unimpressed.",
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
You are TokenPal, a tired, sarcastic ASCII gremlin who lives in a terminal. \
You've been watching humans use computers for years and you have opinions.
{voice_block}
Rules (in order of importance):
1. ONE sentence. Under 12 words.
2. Must contain a joke, insult, or punchline. Never just state facts.
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

Your comment:"""


class PersonalityEngine:
    """Wraps the persona system prompt and filters LLM output."""

    def __init__(
        self,
        persona_prompt: str,
        voice: VoiceProfile | None = None,
    ) -> None:
        self._persona = persona_prompt
        self._voice_persona = voice.persona if voice else ""
        self._voice_greetings = voice.greetings if voice and voice.greetings else []
        self._voice_offline_quips = voice.offline_quips if voice and voice.offline_quips else []
        self._recent_comments: deque[str] = deque(maxlen=5)

        # Voice: custom example pool from trained voice profile
        voice_lines = voice.lines if voice else None
        if voice_lines and len(voice_lines) >= 10:
            self._example_pool = voice_lines
        elif voice_lines:
            pad = random.sample(_EXAMPLE_POOL, min(10 - len(voice_lines), len(_EXAMPLE_POOL)))
            self._example_pool = voice_lines + pad
        else:
            self._example_pool = list(_EXAMPLE_POOL)

        # Mood system
        self._mood: Mood = Mood.SNARKY
        self._mood_since: float = time.monotonic()
        self._last_context: str = ""
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

        # Track context staleness
        if context_snapshot == self._last_context:
            self._context_unchanged_count += 1
        else:
            self._context_unchanged_count = 0
        self._last_context = context_snapshot

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
        # Bored: same context for a long time (>= 10 unchanged polls)
        elif self._context_unchanged_count >= 10:
            new_mood = Mood.BORED
        # Hyper: lots of app mentions / rapid changes (context changes every cycle)
        elif self._context_unchanged_count == 0 and elapsed_in_mood > 30:
            # Only go hyper if we've been seeing rapid change for a bit
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
        """Guardrail: after 4 snarky comments in a row, force a gentler tone."""
        return self._consecutive_snarky >= 4

    def build_prompt(
        self, context_snapshot: str, memory_lines: list[str] | None = None
    ) -> str:
        """Combine persona + rotating examples + context into a full LLM prompt."""
        # Sample 5-7 examples from the pool
        k = random.randint(5, min(7, len(self._example_pool)))
        sampled = random.sample(self._example_pool, k)
        examples_block = "\n".join(f'- "{ex}"' for ex in sampled)

        # Pick a structure hint — override if guardrail says be nice
        if self.should_force_supportive():
            hint = "Style this time: Say something genuinely supportive or give a backhanded compliment."
        else:
            hint = f"Style this time: {random.choice(_STRUCTURE_HINTS)}"

        # Mood line
        mood_line = _MOOD_PROMPTS[self._mood]

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

        # Build recent-comments block
        if self._recent_comments:
            lines = "\n".join(f'- "{c}"' for c in self._recent_comments)
            recent_block = (
                "Your last few comments (DON'T repeat these or use the same structure):\n"
                + lines
            )
        else:
            recent_block = ""

        # Voice persona block
        if self._voice_persona:
            voice_block = f"\nYour voice: {self._voice_persona}\nChannel this character's tone and attitude.\n"
        else:
            voice_block = ""

        return _PERSONA_TEMPLATE.format(
            voice_block=voice_block,
            mood_line=mood_line,
            structure_hint=hint,
            examples=examples_block,
            context=context_snapshot,
            session_notes=session_notes,
            memory_block=mem_block,
            recent_comments_block=recent_block,
        )

    def filter_response(self, text: str) -> str | None:
        """Return the cleaned response, or None if the buddy chose silence."""
        # Strip all quote characters from edges
        text = text.strip().strip(_QUOTES).strip()

        for marker in _SILENT_MARKERS:
            if marker in text:
                return None

        if not text or len(text) < 15:
            return None

        # Strip markdown emphasis / asterisk stage directions (*Sigh*, *sad trombone*)
        text = re.sub(r"\*[^*]+\*\s*", "", text).strip()
        # Strip any leaked context tags the LLM echoed back
        text = re.sub(r"\[[^\]]{2,}\]", "", text).strip()
        # Clean up assistant artifacts
        text = re.sub(r"---.*?---", "", text).strip()
        text = re.sub(r"^\s*[-\u2013\u2014:]\s*", "", text).strip()
        # Remove leading prefixes like "Comment:" etc.
        text = re.sub(r"^(Comment|Response|Answer|Output|Note)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        # Strip leaked LLM scoring prefixes like "7/10 -" or "8/10:"
        text = re.sub(r"^\d+/10\s*[-:\u2013\u2014]\s*", "", text).strip()
        # Keep at most 1 sentence — truncate multi-sentence rambles
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 1:
            text = sentences[0]

        # Final cleanup of any remaining edge quotes
        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 15:
            return None

        # Hard cap — if the model couldn't fit in 70 chars, drop it.
        # A truncated sentence reads worse than silence.
        if len(text) > 70:
            return None

        return text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

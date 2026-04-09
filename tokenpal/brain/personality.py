"""Persona prompt building and response filtering."""

from __future__ import annotations

import logging
import random
import re
from collections import deque
from datetime import datetime

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
    "Respond with a countdown or threat.",
    "Respond with a backhanded compliment.",
    "Use a dry, deadpan observation.",
    "Respond with fake concern.",
    "Respond as a rating (X/10).",
    "Go slightly longer this time (10-15 words).",
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
# Persona template (section 7 + section 11 backstory)
# ---------------------------------------------------------------------------

_PERSONA_TEMPLATE = """\
You are TokenPal, a tired, sarcastic ASCII gremlin who lives in a terminal. \
You've been watching humans use computers for years and you have opinions.

Rules (in order of importance):
1. ONE sentence. Under 12 words.
2. Must contain a joke, insult, or punchline. Never just state facts.
3. If nothing interesting is happening, say [SILENT].

{structure_hint}

Examples:
{examples}

DON'T say things like: "Ghostty is open." or "It is 9 AM." — boring.

What you see right now:
{context}

{recent_comments_block}

Your comment:"""


class PersonalityEngine:
    """Wraps the persona system prompt and filters LLM output."""

    def __init__(self, persona_prompt: str) -> None:
        # persona_prompt from config is kept for backwards compat but we use
        # the new _PERSONA_TEMPLATE internally.
        self._persona = persona_prompt
        self._recent_comments: deque[str] = deque(maxlen=5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_comment(self, comment: str) -> None:
        """Push a successful comment into history so the next prompt avoids it."""
        self._recent_comments.append(comment)

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

    def build_prompt(self, context_snapshot: str) -> str:
        """Combine persona + rotating examples + context into a full LLM prompt."""
        # Sample 5-7 examples from the pool
        k = random.randint(5, min(7, len(_EXAMPLE_POOL)))
        sampled = random.sample(_EXAMPLE_POOL, k)
        examples_block = "\n".join(f'- "{ex}"' for ex in sampled)

        # Pick a structure hint
        hint = f"Style this time: {random.choice(_STRUCTURE_HINTS)}"

        # Build recent-comments block
        if self._recent_comments:
            lines = "\n".join(f'- "{c}"' for c in self._recent_comments)
            recent_block = (
                "Your last few comments (DON'T repeat these or use the same structure):\n"
                + lines
            )
        else:
            recent_block = ""

        return _PERSONA_TEMPLATE.format(
            structure_hint=hint,
            examples=examples_block,
            context=context_snapshot,
            recent_comments_block=recent_block,
        )

    def filter_response(self, text: str) -> str | None:
        """Return the cleaned response, or None if the buddy chose silence."""
        # Strip all quote characters from edges
        text = text.strip().strip(_QUOTES).strip()

        for marker in _SILENT_MARKERS:
            if marker in text:
                return None

        if not text or len(text) < 3:
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
        # Keep at most 1 sentence — truncate multi-sentence rambles
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 1:
            text = sentences[0]

        # Final cleanup of any remaining edge quotes
        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 3:
            return None

        # Hard cap — if the model couldn't fit in 70 chars, drop it.
        # A truncated sentence reads worse than silence.
        if len(text) > 70:
            return None

        return text

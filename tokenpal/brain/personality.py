"""Persona prompt building and response filtering."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_SILENT_MARKERS = ["[SILENT]", "[silent]", "SILENT"]

# All flavors of quotation marks
_QUOTES = '"\'\u201c\u201d\u2018\u2019\u00ab\u00bb'


class PersonalityEngine:
    """Wraps the persona system prompt and filters LLM output."""

    def __init__(self, persona_prompt: str) -> None:
        self._persona = persona_prompt

    def build_prompt(self, context_snapshot: str) -> str:
        """Combine persona + current context into a full LLM prompt."""
        return (
            f"{self._persona}\n\n"
            f"What you see right now:\n"
            f"{context_snapshot}\n\n"
            f"Your comment (one short sentence, under 12 words):"
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

        # Strip any leaked context tags the LLM echoed back
        text = re.sub(r"\[[^\]]{2,}\]", "", text).strip()
        # Clean up assistant artifacts
        text = re.sub(r"---.*?---", "", text).strip()
        text = re.sub(r"^\s*[-\u2013\u2014:]\s*", "", text).strip()
        # Remove leading prefixes like "Comment:" etc.
        text = re.sub(r"^(Comment|Response|Answer|Output|Note)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        # Only truncate if the model really rambles (3+ sentences)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 2:
            text = " ".join(sentences[:2])

        # Final cleanup of any remaining edge quotes
        text = text.strip(_QUOTES).strip()

        if not text or len(text) < 3:
            return None

        if len(text) > 80:
            text = text[:77] + "..."

        return text

"""Persona prompt building and response filtering."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_SILENT_MARKERS = ["[SILENT]", "[silent]", "SILENT"]


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
        # Strip wrapping quotes (gemma often wraps in double quotes)
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        # Also strip leading/trailing stray quotes
        text = text.strip('"').strip("'").strip(""").strip(""").strip()

        for marker in _SILENT_MARKERS:
            if marker in text:
                return None

        if not text or len(text) < 3:
            return None

        import re

        # Strip any leaked context tags the LLM echoed back
        text = re.sub(r"\[[^\]]{2,}\]", "", text).strip()
        # Clean up dashes, "SOLUTION", "ANSWER" and other assistant artifacts
        text = re.sub(r"---.*?---", "", text).strip()
        text = re.sub(r"^\s*[-–—:]\s*", "", text).strip()
        # Remove leading "Comment:" or "Response:" prefixes
        text = re.sub(r"^(Comment|Response|Answer|Output|Note)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        # Take only the first sentence if the model rambles
        first_sentence = re.split(r"(?<=[.!?])\s+", text)
        if first_sentence:
            text = first_sentence[0]

        if not text or len(text) < 3:
            return None

        # Cap length
        if len(text) > 80:
            text = text[:77] + "..."

        return text

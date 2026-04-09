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
            f"--- CURRENT OBSERVATIONS ---\n"
            f"{context_snapshot}\n"
            f"--- END OBSERVATIONS ---\n\n"
            f"Write ONE short, funny comment (under 15 words) about what you observe. "
            f"Do NOT repeat or echo the observations. Do NOT include tags like [TIME] or [APP]. "
            f"Just say your comment directly as if speaking out loud. "
            f"If nothing is interesting, respond with only: [SILENT]"
        )

    def filter_response(self, text: str) -> str | None:
        """Return the cleaned response, or None if the buddy chose silence."""
        text = text.strip().strip('"').strip("'")

        for marker in _SILENT_MARKERS:
            if marker in text:
                return None

        if not text or len(text) < 3:
            return None

        # Strip any leaked context tags the LLM echoed back
        import re
        text = re.sub(r"\[[^\]]{2,}\]", "", text).strip()
        # Clean up leftover dashes/whitespace from stripped tags
        text = re.sub(r"^\s*[-–—]\s*", "", text).strip()

        if not text or len(text) < 3:
            return None

        # Cap length to keep comments punchy
        if len(text) > 80:
            text = text[:77] + "..."

        return text

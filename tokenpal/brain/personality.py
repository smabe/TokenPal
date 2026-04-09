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
            f"Here is what you can currently observe:\n"
            f"{context_snapshot}\n\n"
            f"Based on what you observe, make a short comment. "
            f"If nothing is interesting, respond with [SILENT]."
        )

    def filter_response(self, text: str) -> str | None:
        """Return the cleaned response, or None if the buddy chose silence."""
        text = text.strip().strip('"').strip("'")

        for marker in _SILENT_MARKERS:
            if marker in text:
                return None

        if not text or len(text) < 3:
            return None

        # Cap at ~100 chars to keep comments punchy
        if len(text) > 120:
            text = text[:117] + "..."

        return text

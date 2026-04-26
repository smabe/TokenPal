"""Lobsters sense — polls lobste.rs hottest page for ambient tech context."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.lobsters._client import fetch_top_stories
from tokenpal.senses.registry import register_sense
from tokenpal.util.text_guards import truncate_ellipsis

log = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 80
_HEADLINE_LIMIT = 3


@register_sense
class LobstersSense(AbstractSense):
    sense_name = "lobsters"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 900.0
    reading_ttl_s = 3600.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_summary: str = ""

    async def setup(self) -> None:
        log.info("Lobsters sense ready — hottest page, poll 15min")

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        stories = [
            s for s in fetch_top_stories(limit=_HEADLINE_LIMIT)
            if not contains_sensitive_content_term(s.title)
        ]
        if not stories:
            return None

        formatted = [
            f"'{truncate_ellipsis(s.title, _TITLE_MAX_CHARS)}' — {s.score} pts"
            for s in stories
        ]
        summary = "Top Lobsters: " + " | ".join(formatted)
        if summary == self._prev_summary:
            return None
        self._prev_summary = summary

        data: dict[str, Any] = {
            "stories": [
                {"title": s.title, "score": s.score, "url": s.url}
                for s in stories
            ],
        }
        return self._reading(data=data, summary=summary, confidence=1.0)

    async def teardown(self) -> None:
        pass

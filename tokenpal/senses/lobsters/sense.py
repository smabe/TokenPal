"""Lobsters sense — polls lobste.rs hottest page for ambient tech context."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.lobsters._client import fetch_top_story
from tokenpal.senses.registry import register_sense
from tokenpal.util.text_guards import truncate_ellipsis

log = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 80


@register_sense
class LobstersSense(AbstractSense):
    sense_name = "lobsters"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 1800.0
    reading_ttl_s = 7200.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_summary: str = ""

    async def setup(self) -> None:
        log.info("Lobsters sense ready — hottest page, poll 30min")

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        story = fetch_top_story()
        if story is None:
            return None

        if contains_sensitive_content_term(story.title):
            log.debug(
                "Lobsters story dropped (sensitive term): %s",
                story.title[:_TITLE_MAX_CHARS],
            )
            return None

        summary = (
            f"Top Lobsters: '{truncate_ellipsis(story.title, _TITLE_MAX_CHARS)}' "
            f"— {story.score} points"
        )
        if summary == self._prev_summary:
            return None
        self._prev_summary = summary

        data: dict[str, Any] = {
            "title": story.title,
            "score": story.score,
            "url": story.url,
        }
        return self._reading(data=data, summary=summary, confidence=1.0)

    async def teardown(self) -> None:
        pass

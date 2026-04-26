"""World awareness sense — polls HN front page for ambient tech context."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense
from tokenpal.senses.world_awareness.hn_client import fetch_top_story

log = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 80


@register_sense
class WorldAwarenessSense(AbstractSense):
    sense_name = "world_awareness"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 1800.0  # 30 minutes
    reading_ttl_s = 7200.0  # 2 hours — 2× poll interval staleness guardrail

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_summary: str = ""

    async def setup(self) -> None:
        log.info("World awareness sense ready — HN front page, poll 30min")

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        story = fetch_top_story()
        if story is None:
            return None

        if contains_sensitive_content_term(story.title):
            log.debug("HN story dropped (sensitive term): %s", story.title[:_TITLE_MAX_CHARS])
            return None

        truncated = story.title
        if len(truncated) > _TITLE_MAX_CHARS:
            truncated = truncated[: _TITLE_MAX_CHARS - 1].rstrip() + "…"

        summary = f"Top HN: '{truncated}' — {story.points} points"
        if summary == self._prev_summary:
            return None
        self._prev_summary = summary

        data: dict[str, Any] = {
            "title": story.title,
            "points": story.points,
            "url": story.url,
            "author": story.author,
            "created_at": story.created_at,
        }

        return self._reading(data=data, summary=summary, confidence=1.0)

    async def teardown(self) -> None:
        pass

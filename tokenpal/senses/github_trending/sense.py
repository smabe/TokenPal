"""GitHub trending sense — top new repo this week."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.github_trending._client import fetch_top_repo
from tokenpal.senses.registry import register_sense
from tokenpal.util.text_guards import truncate_ellipsis

log = logging.getLogger(__name__)

_DESC_MAX_CHARS = 80


@register_sense
class GitHubTrendingSense(AbstractSense):
    sense_name = "github_trending"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 1800.0
    reading_ttl_s = 7200.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_summary: str = ""

    async def setup(self) -> None:
        log.info("GitHub trending sense ready — search API, poll 30min")

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        repo = fetch_top_repo()
        if repo is None:
            return None

        if contains_sensitive_content_term(f"{repo.full_name} {repo.description}"):
            log.debug("GitHub repo dropped (sensitive term): %s", repo.full_name)
            return None

        desc = truncate_ellipsis(repo.description, _DESC_MAX_CHARS)
        lang = f" ({repo.language})" if repo.language else ""
        suffix = f" — {desc}" if desc else ""
        summary = (
            f"Trending GitHub: {repo.full_name} — {repo.stars} stars (last 7d)"
            f"{lang}{suffix}"
        )

        if summary == self._prev_summary:
            return None
        self._prev_summary = summary

        data: dict[str, Any] = {
            "full_name": repo.full_name,
            "stars": repo.stars,
            "description": repo.description,
            "language": repo.language,
            "url": repo.url,
        }
        return self._reading(data=data, summary=summary, confidence=1.0)

    async def teardown(self) -> None:
        pass

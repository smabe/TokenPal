"""GitHub trending sense — top new repos this week."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.github_trending._client import GHRepo, fetch_top_repos
from tokenpal.senses.registry import register_sense
from tokenpal.util.text_guards import truncate_ellipsis

log = logging.getLogger(__name__)

_DESC_MAX_CHARS = 60
_REPO_LIMIT = 3


def _format_repo(repo: GHRepo) -> str:
    desc = truncate_ellipsis(repo.description, _DESC_MAX_CHARS)
    lang = f" ({repo.language})" if repo.language else ""
    suffix = f" — {desc}" if desc else ""
    return f"{repo.full_name} — {repo.stars}★{lang}{suffix}"


@register_sense
class GitHubTrendingSense(AbstractSense):
    sense_name = "github_trending"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 900.0
    reading_ttl_s = 3600.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_summary: str = ""

    async def setup(self) -> None:
        log.info("GitHub trending sense ready — search API, poll 15min")

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        repos = [
            r for r in fetch_top_repos(limit=_REPO_LIMIT)
            if not contains_sensitive_content_term(f"{r.full_name} {r.description}")
        ]
        if not repos:
            return None

        summary = "Trending GitHub (last 7d): " + " | ".join(_format_repo(r) for r in repos)
        if summary == self._prev_summary:
            return None
        self._prev_summary = summary

        data: dict[str, Any] = {
            "repos": [
                {
                    "full_name": r.full_name,
                    "stars": r.stars,
                    "description": r.description,
                    "language": r.language,
                    "url": r.url,
                }
                for r in repos
            ],
        }
        return self._reading(data=data, summary=summary, confidence=1.0)

    async def teardown(self) -> None:
        pass

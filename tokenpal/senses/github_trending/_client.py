"""GitHub trending repo client.

Polls GitHub's keyless Search API for the single most-starred repo
created in the last 7 days.

IMPORTANT: returned repo names + descriptions are untrusted user-authored
content — callers must wrap them in delimiters and apply a banned-word
filter before feeding them to any LLM prompt.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from tokenpal.util.http_json import http_json

log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.github.com/search/repositories"


@dataclass
class GHRepo:
    full_name: str
    stars: int
    description: str
    language: str
    url: str


def _trending_url(today: dt.date, per_page: int) -> str:
    cutoff = (today - dt.timedelta(days=7)).isoformat()
    return (
        f"{_SEARCH_URL}?q=created:>{cutoff}&sort=stars&order=desc&per_page={per_page}"
    )


def _parse_repo(item: dict) -> GHRepo | None:
    full_name = item.get("full_name") or ""
    if not full_name:
        return None

    stars_raw = item.get("stargazers_count") or 0
    try:
        stars = int(stars_raw)
    except (TypeError, ValueError):
        stars = 0

    return GHRepo(
        full_name=full_name,
        stars=stars,
        description=item.get("description") or "",
        language=item.get("language") or "",
        url=item.get("html_url") or "",
    )


def fetch_top_repos(
    limit: int, today: dt.date | None = None,
) -> list[GHRepo]:
    """Return up to *limit* most-starred repos created in the last 7 days."""
    raw = http_json(_trending_url(today or dt.date.today(), per_page=limit))
    if not isinstance(raw, dict):
        return []
    items = raw.get("items") or []
    return [r for item in items if isinstance(item, dict) if (r := _parse_repo(item))]

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


def _trending_url(today: dt.date) -> str:
    cutoff = (today - dt.timedelta(days=7)).isoformat()
    return f"{_SEARCH_URL}?q=created:>{cutoff}&sort=stars&order=desc&per_page=1"


def fetch_top_repo(today: dt.date | None = None) -> GHRepo | None:
    """Return the most-starred repo created in the last 7 days, or None on failure."""
    raw = http_json(_trending_url(today or dt.date.today()))
    if not isinstance(raw, dict):
        return None

    items = raw.get("items") or []
    if not items:
        return None

    top = items[0]
    if not isinstance(top, dict):
        return None

    full_name = top.get("full_name") or ""
    if not full_name:
        return None

    stars_raw = top.get("stargazers_count") or 0
    try:
        stars = int(stars_raw)
    except (TypeError, ValueError):
        stars = 0

    return GHRepo(
        full_name=full_name,
        stars=stars,
        description=top.get("description") or "",
        language=top.get("language") or "",
        url=top.get("html_url") or "",
    )

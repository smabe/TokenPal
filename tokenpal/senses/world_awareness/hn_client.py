"""Hacker News Algolia API client.

Outbound network module. IMPORTANT: returned titles are untrusted user-authored
content — callers must wrap them in delimiters and apply a banned-word filter
before feeding them to any LLM prompt.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from tokenpal.util.http_json import http_json

log = logging.getLogger(__name__)

_HN_FRONT_PAGE_URL = (
    "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=5"
)


@dataclass
class HNStory:
    title: str
    points: int
    url: str
    author: str
    created_at: str


def _normalize_title(raw: str) -> str:
    if not raw:
        return ""
    return " ".join(html.unescape(raw).split())


def fetch_top_story() -> HNStory | None:
    """Fetch HN front page and return the top story, or None on failure."""
    raw = http_json(_HN_FRONT_PAGE_URL)
    if not isinstance(raw, dict):
        return None

    hits = raw.get("hits") or []
    if not hits:
        return None

    top = hits[0]
    title = _normalize_title(top.get("title") or top.get("story_title") or "")
    if not title:
        return None

    points_raw = top.get("points") or top.get("story_points") or 0
    try:
        points = int(points_raw)
    except (TypeError, ValueError):
        points = 0

    url = top.get("url") or top.get("story_url") or ""
    if not isinstance(url, str):
        url = ""
    author = top.get("author") or ""
    if not isinstance(author, str):
        author = ""
    created_at = top.get("created_at") or ""
    if not isinstance(created_at, str):
        created_at = ""

    return HNStory(
        title=title,
        points=points,
        url=url,
        author=author,
        created_at=created_at,
    )

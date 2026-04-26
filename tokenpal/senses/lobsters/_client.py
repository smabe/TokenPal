"""Lobsters hottest-page JSON client.

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

_LOBSTERS_HOTTEST_URL = "https://lobste.rs/hottest.json"


@dataclass
class LobstersStory:
    title: str
    score: int
    url: str


def _normalize_title(raw: str) -> str:
    if not raw:
        return ""
    return " ".join(html.unescape(raw).split())


def _parse_story(item: dict) -> LobstersStory | None:
    title = _normalize_title(item.get("title") or "")
    if not title:
        return None

    score_raw = item.get("score") or 0
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0

    url = item.get("url") or item.get("short_id_url") or ""
    if not isinstance(url, str):
        url = ""

    return LobstersStory(title=title, score=score, url=url)


def fetch_top_stories(limit: int) -> list[LobstersStory]:
    """Fetch lobste.rs hottest list and return up to *limit* parsed stories."""
    raw = http_json(_LOBSTERS_HOTTEST_URL)
    if not isinstance(raw, list):
        return []
    stories = [s for item in raw if isinstance(item, dict) if (s := _parse_story(item))]
    return stories[:limit]

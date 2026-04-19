"""Hacker News search via Algolia API.

Keyless, free, no quota cap worth worrying about. Used as a topic-specific
backend for tech-news / show-HN style queries routed by the planner.

HN stories link to an external URL (the submitted article); Ask HN / Show HN
posts have no external URL, so we fall back to the HN item permalink. The
downstream /research fetch chain pulls the actual article content.

All network failures are caught and returned as empty lists. Never raises.
"""

from __future__ import annotations

import html
import logging
import urllib.parse
from typing import Any

from tokenpal.senses.web_search._http import http_json

log = logging.getLogger(__name__)

_API_URL = "https://hn.algolia.com/api/v1/search"
_ITEM_URL_FMT = "https://news.ycombinator.com/item?id={}"


def _normalize(s: str) -> str:
    return " ".join(html.unescape(s).split()).strip()


def hn_search(
    query: str,
    *,
    hits_per_page: int = 6,
    timeout_s: float = 10.0,
) -> list[dict[str, Any]]:
    """GET Algolia HN /search (restricted to stories) and return cleaned hits.

    Each hit dict has `url`, `title`, `description`. Returns [] on any error.
    """
    query = (query or "").strip()
    if not query:
        return []

    params = urllib.parse.urlencode({
        "query": query,
        "tags": "story",
        "hitsPerPage": max(1, min(hits_per_page, 20)),
    })
    payload = http_json(f"{_API_URL}?{params}", timeout_s=timeout_s)

    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    if not isinstance(hits, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in hits:
        if not isinstance(item, dict):
            continue
        title = _normalize(str(item.get("title") or item.get("story_title") or ""))
        if not title:
            continue
        object_id = str(item.get("objectID") or "").strip()
        url_val = str(item.get("url") or item.get("story_url") or "").strip()
        if not url_val and object_id:
            url_val = _ITEM_URL_FMT.format(object_id)
        if not url_val:
            continue
        # Ask HN / Show HN bodies live on `story_text`; for link-posts fall
        # back to a signal-carrying description so the snippet isn't empty.
        body = _normalize(str(item.get("story_text") or ""))
        if not body:
            points = item.get("points") or 0
            comments = item.get("num_comments") or 0
            body = f"HN discussion: {points} points, {comments} comments"
        cleaned.append({
            "url": url_val,
            "title": title,
            "description": body,
        })
    return cleaned

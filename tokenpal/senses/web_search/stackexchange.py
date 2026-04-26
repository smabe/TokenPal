"""Stack Exchange search via API 2.3.

Keyless (anonymous): ~300 requests/day per IP. Used as a topic-specific
backend for technical how-to / code-help queries routed by the planner.

We query stackoverflow by default; the API returns question metadata only
(title, link, tags, score), not the post body. The downstream /research
fetch chain pulls the actual Q&A content from the `link` URL.

On quota throttle (HTTP 400 / `backoff` field) or any network failure we
return an empty list — caller falls back to DDG. Never raises on I/O.
"""

from __future__ import annotations

import html
import logging
import urllib.parse
from typing import Any

from tokenpal.util.http_json import http_json

log = logging.getLogger(__name__)

_API_URL = "https://api.stackexchange.com/2.3/search/advanced"


def _normalize(s: str) -> str:
    return " ".join(html.unescape(s).split()).strip()


def stackexchange_search(
    query: str,
    *,
    site: str = "stackoverflow",
    pagesize: int = 6,
    timeout_s: float = 10.0,
) -> list[dict[str, Any]]:
    """GET SE /search/advanced and return cleaned hits.

    Each hit dict has `url`, `title`, `description`. Returns [] on any error
    (including quota throttle and server-side `backoff` throttle signals).
    """
    query = (query or "").strip()
    if not query:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        "site": site,
        "order": "desc",
        "sort": "relevance",
        "pagesize": max(1, min(pagesize, 20)),
    })
    payload = http_json(f"{_API_URL}?{params}", timeout_s=timeout_s)

    if not isinstance(payload, dict):
        return []
    # Any `backoff` signal = SE throttled us; return empty so dispatcher falls back.
    if payload.get("backoff"):
        log.debug("stackexchange: backoff signal, returning empty")
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _normalize(str(item.get("title") or ""))
        link = str(item.get("link") or "").strip()
        if not title or not link:
            continue
        tags = item.get("tags") or []
        tag_str = ", ".join(str(t) for t in tags if isinstance(t, str))[:120]
        score = item.get("score") or 0
        answers = item.get("answer_count") or 0
        answered = "answered" if item.get("is_answered") else "unanswered"
        description = (
            f"SO: {score} votes, {answers} answers ({answered})"
            + (f" — tags: {tag_str}" if tag_str else "")
        )
        cleaned.append({
            "url": link,
            "title": title,
            "description": description,
        })
    return cleaned

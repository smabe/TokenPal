"""Brave Search API client.

Brave's Web Search API is keyed (free tier: 2,000 queries/month, 1 query
per second). We use it as a free-tier alternative to DDG when the user
provides a key — higher-quality ranking than DDG Lite scraping, no opaque
rate limits.

Unlike Tavily, Brave does NOT pre-extract article content; the response
carries only title + URL + short description. So BraveBackend behaves like
DuckDuckGoBackend downstream: the /research pipeline still runs its local
fetch+extract chain on each Brave-sourced URL.

All network failures are caught and returned as empty result lists — callers
fall back to other backends. This module never raises on I/O.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from tokenpal.senses.web_search._http import http_json

log = logging.getLogger(__name__)

_API_URL = "https://api.search.brave.com/res/v1/web/search"


def brave_search(
    query: str,
    api_key: str,
    *,
    count: int = 6,
    timeout_s: float = 10.0,
) -> list[dict[str, Any]]:
    """GET Brave /web/search and return the `web.results` list.

    Each result dict has at minimum `url`, `title`, `description`. Returns
    an empty list on any network, auth, parse, or schema failure.
    """
    query = (query or "").strip()
    key = (api_key or "").strip()
    if not query or not key:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        # Brave caps count at 20; clamp defensively even though the planner
        # never goes that high.
        "count": max(1, min(count, 20)),
    })
    payload = http_json(
        f"{_API_URL}?{params}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
        timeout_s=timeout_s,
    )

    if not isinstance(payload, dict):
        return []
    web = payload.get("web")
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url_val = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if not url_val or not description:
            continue
        cleaned.append({
            "url": url_val,
            "title": title or url_val,
            "description": description,
        })
    return cleaned

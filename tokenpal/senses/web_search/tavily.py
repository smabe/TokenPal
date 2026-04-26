"""Tavily Search API client.

Tavily is an LLM-optimized search service: one HTTP call returns a list of
results WITH pre-extracted article content, scoring, and optional summary
answer. We use it as a drop-in replacement for the local DDG + newspaper4k
fetch/extract chain when the user opts in via /cloud tavily.

Pricing:
    basic   — 1 credit/query
    advanced — 2 credits/query (deeper extraction; our default)
Tavily's free tier is ~1,000 credits/month.

All network failures are caught and returned as empty result lists — the
caller falls back to DDG + local fetch. This module never raises on I/O.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tokenpal.util.http_json import http_json

log = logging.getLogger(__name__)

_API_URL = "https://api.tavily.com/search"


def tavily_search(
    query: str,
    api_key: str,
    *,
    search_depth: str = "advanced",
    max_results: int = 6,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """POST to Tavily /search and return the raw results list.

    Each result is a dict with at least `url`, `title`, `content` (the
    extracted page body — NOT a snippet) and `score`. Returns an empty list
    on any network, auth, parse, or schema failure.
    """
    query = (query or "").strip()
    key = (api_key or "").strip()
    if not query or not key:
        return []

    body = json.dumps({
        "query": query,
        "api_key": key,
        "search_depth": search_depth,
        "max_results": max(1, min(max_results, 10)),
        # We do our own synth downstream; don't pay for Tavily's answer
        # synthesis (it's a separate credit charge on some plans).
        "include_answer": False,
        "include_raw_content": False,
    }).encode("utf-8")

    payload = http_json(
        _API_URL,
        method="POST",
        body=body,
        headers={"Content-Type": "application/json"},
        timeout_s=timeout_s,
    )

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not content:
            # No URL or no extracted text — useless to us even if Tavily
            # returned something like a stub result.
            continue
        cleaned.append({
            "url": url,
            "title": title or url,
            "content": content,
            "score": float(item.get("score") or 0.0),
        })
    return cleaned

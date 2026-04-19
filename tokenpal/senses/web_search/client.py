"""Web search backend for /ask.

WARNING: this module makes outbound network calls. All returned text is
untrusted user-authored content — callers MUST wrap in delimiters and apply
the banned-word filter (see tokenpal.brain.personality.SENSITIVE_APPS) before
composing any LLM prompt.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

BackendName = Literal["duckduckgo", "wikipedia", "brave", "tavily"]

log = logging.getLogger(__name__)

# Callers should truncate untrusted text to this length before writing to logs.
# (Separate from the 500-char truncation applied to the LLM-bound `text` field.)
LOG_TRUNCATE_CHARS = 80

_MAX_TEXT_CHARS = 500
_MAX_HTML_BYTES = 512 * 1024
_HTTP_TIMEOUT_S = 10.0
_USER_AGENT = "TokenPal/1.0 (+https://github.com/smabe/TokenPal)"


@dataclass
class SearchResult:
    query: str
    backend: BackendName
    title: str
    text: str
    source_url: str
    # Full extracted article body when the backend has already done its own
    # extraction (Tavily). NEVER truncated — callers that want a preview
    # should read `text` instead. When non-empty, downstream pipelines can
    # skip their local fetch+extract stage entirely.
    preloaded_content: str = ""


class SearchBackend(ABC):
    @abstractmethod
    def search(self, query: str) -> SearchResult | None: ...


def _http_get_json(url: str) -> dict[str, Any] | None:
    """GET a URL and parse JSON. Returns None on any network/parse error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
        return json.loads(raw)  # type: ignore[no-any-return]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as e:
        log.debug("HTTP GET failed for %s: %s", url.split("?")[0], e)
        return None
    except Exception as e:  # noqa: BLE001 — network code must never raise
        log.debug("Unexpected error fetching %s: %s", url.split("?")[0], e)
        return None


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    return text[:_MAX_TEXT_CHARS].rstrip() + "…"


# DDG Instant Answer only returns infobox facts. For natural-language queries,
# scrape the first result snippet from lite.duckduckgo.com (a minimal-HTML
# search results page designed to be easy to parse).
_DDG_LITE_LINK_RE = re.compile(
    r'<a\b[^>]*?\bhref=["\']([^"\']+)["\'][^>]*?\bclass=["\']result-link["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_LITE_SNIPPET_RE = re.compile(
    r"""<td\b[^>]*?\bclass=["']result-snippet["'][^>]*>(.*?)</td>""",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", s)).strip()


def _ddg_lite_fetch_body(query: str) -> str | None:
    """POST the query to DDG Lite and return the raw HTML body, or None."""
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://lite.duckduckgo.com/lite/",
            data=data,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return resp.read(_MAX_HTML_BYTES).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        log.debug("DDG lite fetch failed: %s", e)
        return None


def _ddg_unwrap_redirect(href: str) -> str:
    """DDG Lite wraps outbound URLs in /l/?uddg=<target>. Unwrap to the real URL."""
    uddg = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0]
    return uddg or href


def _ddg_lite_first_result(query: str) -> tuple[str, str, str] | None:
    """Return (title, snippet, url) of the first DDG Lite result, or None."""
    body = _ddg_lite_fetch_body(query)
    if not body or "result-link" not in body:
        return None

    link = _DDG_LITE_LINK_RE.search(body)
    snip = _DDG_LITE_SNIPPET_RE.search(body)
    if not link or not snip:
        return None

    title = _strip_html(link.group(2))
    snippet = _strip_html(snip.group(1))
    if not title or not snippet:
        return None
    return title, snippet, _ddg_unwrap_redirect(link.group(1))


def _ddg_lite_all_results(query: str, limit: int) -> list[tuple[str, str, str]]:
    """Return up to `limit` (title, snippet, url) tuples from DDG Lite.

    Links and snippets appear in the HTML in matched order, so we zip by
    position. Skips rows where either half is empty.
    """
    body = _ddg_lite_fetch_body(query)
    if not body or "result-link" not in body:
        return []

    links = _DDG_LITE_LINK_RE.findall(body)
    snips = _DDG_LITE_SNIPPET_RE.findall(body)
    out: list[tuple[str, str, str]] = []
    for (href, raw_title), raw_snip in zip(links, snips):
        if len(out) >= limit:
            break
        title = _strip_html(raw_title)
        snippet = _strip_html(raw_snip)
        if not title or not snippet:
            continue
        out.append((title, snippet, _ddg_unwrap_redirect(href)))
    return out


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo Instant Answer API. Free, keyless."""

    backend_name = "duckduckgo"

    def search(self, query: str) -> SearchResult | None:
        q = urllib.parse.quote_plus(query)
        url = (
            f"https://api.duckduckgo.com/?q={q}"
            f"&format=json&no_html=1&skip_disambig=1"
        )
        data = _http_get_json(url)
        if not data:
            return None

        text = (data.get("AbstractText") or "").strip()
        title = (data.get("Heading") or "").strip()
        source_url = (data.get("AbstractURL") or "").strip()

        if not text:
            related = data.get("RelatedTopics") or []
            if related and isinstance(related, list):
                first = related[0]
                if isinstance(first, dict):
                    text = (first.get("Text") or "").strip()
                    if not source_url:
                        source_url = (first.get("FirstURL") or "").strip()

        if not text:
            hit = _ddg_lite_first_result(query)
            if hit is None:
                return None
            title, text, source_url = hit

        return SearchResult(
            query=query,
            backend=self.backend_name,
            title=title,
            text=_truncate(text),
            source_url=source_url,
        )


class WikipediaBackend(SearchBackend):
    """Wikipedia REST summary. Free, keyless. Good fallback when DDG whiffs."""

    backend_name = "wikipedia"

    def search(self, query: str) -> SearchResult | None:
        # Wikipedia's summary endpoint expects the article title path-segment.
        title_path = urllib.parse.quote(query.strip().replace(" ", "_"), safe="")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title_path}"
        data = _http_get_json(url)
        if not data:
            return None

        extract = (data.get("extract") or "").strip()
        if not extract:
            return None

        title = (data.get("title") or "").strip()
        content_urls = data.get("content_urls") or {}
        desktop = content_urls.get("desktop") if isinstance(content_urls, dict) else None
        source_url = ""
        if isinstance(desktop, dict):
            source_url = (desktop.get("page") or "").strip()

        return SearchResult(
            query=query,
            backend=self.backend_name,
            title=title,
            text=_truncate(extract),
            source_url=source_url,
        )


class BraveBackend(SearchBackend):
    """Brave Search API — stub. Reads key from TOKENPAL_BRAVE_KEY env (priority)
    or `api_key` constructor arg. Not yet implemented."""

    backend_name = "brave"

    def __init__(self, api_key: str = "") -> None:
        env_key = os.environ.get("TOKENPAL_BRAVE_KEY", "").strip()
        # Env var takes priority over passed arg.
        self._api_key = env_key or (api_key or "").strip()

    def search(self, query: str) -> SearchResult | None:
        raise NotImplementedError(
            "Brave API key-based backend not yet implemented"
        )


class TavilyBackend(SearchBackend):
    """Tavily Search API. Returns results with `preloaded_content` populated
    (full pre-extracted article body), so the /research pipeline can skip its
    local fetch+extract chain for Tavily-sourced hits.

    Key resolution order:
        1. `api_key` constructor arg (the /cloud tavily store path)
        2. TOKENPAL_TAVILY_KEY env var (dev/debug fallback)
    """

    backend_name = "tavily"

    def __init__(
        self,
        api_key: str = "",
        *,
        search_depth: str = "advanced",
        max_results: int = 6,
        timeout_s: float = 15.0,
    ) -> None:
        env_key = os.environ.get("TOKENPAL_TAVILY_KEY", "").strip()
        self._api_key = (api_key or "").strip() or env_key
        self._search_depth = search_depth
        self._max_results = max_results
        self._timeout_s = timeout_s

    def search(self, query: str) -> SearchResult | None:
        results = self.search_all(query, limit=1)
        return results[0] if results else None

    def search_all(self, query: str, limit: int) -> list[SearchResult]:
        from tokenpal.senses.web_search.tavily import tavily_search

        if not self._api_key:
            log.debug("tavily: no api key configured, returning empty")
            return []
        hits = tavily_search(
            query,
            api_key=self._api_key,
            search_depth=self._search_depth,
            max_results=min(limit, self._max_results),
            timeout_s=self._timeout_s,
        )
        out: list[SearchResult] = []
        for hit in hits:
            content = hit["content"]
            out.append(SearchResult(
                query=query,
                backend="tavily",
                title=hit["title"],
                text=_truncate(content),  # short snippet for logging/display
                source_url=hit["url"],
                preloaded_content=content,  # full body, NEVER truncated
            ))
        return out


def search(
    query: str,
    backend: str = "duckduckgo",
    brave_api_key: str = "",
    tavily_api_key: str = "",
    tavily_search_depth: str = "advanced",
    tavily_max_results: int = 6,
    tavily_timeout_s: float = 15.0,
) -> SearchResult | None:
    """Dispatch to the named backend. Falls back to Wikipedia if DDG has no answer.
    Returns None on all-backend failure. NEVER raises on network errors — returns None."""
    query = (query or "").strip()
    if not query:
        return None

    name = (backend or "duckduckgo").lower()

    if name == "brave":
        be = BraveBackend(api_key=brave_api_key)
        return be.search(query)

    if name == "tavily":
        try:
            return TavilyBackend(
                api_key=tavily_api_key,
                search_depth=tavily_search_depth,
                max_results=tavily_max_results,
                timeout_s=tavily_timeout_s,
            ).search(query)
        except Exception as e:  # noqa: BLE001
            log.debug("Tavily backend error: %s", e)
            return None

    if name == "wikipedia":
        try:
            return WikipediaBackend().search(query)
        except Exception as e:  # noqa: BLE001 — network path must not raise
            log.debug("Wikipedia backend error: %s", e)
            return None

    # Default path: DuckDuckGo, then Wikipedia fallback.
    try:
        ddg_result = DuckDuckGoBackend().search(query)
    except Exception as e:  # noqa: BLE001
        log.debug("DuckDuckGo backend error: %s", e)
        ddg_result = None

    if ddg_result is not None:
        return ddg_result

    try:
        return WikipediaBackend().search(query)
    except Exception as e:  # noqa: BLE001
        log.debug("Wikipedia fallback error: %s", e)
        return None


def search_many(
    query: str,
    backend: str = "duckduckgo",
    limit: int = 5,
    *,
    tavily_api_key: str = "",
    tavily_search_depth: str = "advanced",
    tavily_timeout_s: float = 15.0,
) -> list[SearchResult]:
    """Return up to `limit` results for a query. DuckDuckGo Lite and Tavily
    return multiple; Wikipedia's summary endpoint is inherently 1:1 and
    wraps its single result (or nothing) in a list.
    NEVER raises on network errors — returns an empty list.
    """
    query = (query or "").strip()
    if not query or limit <= 0:
        return []

    name = (backend or "duckduckgo").lower()

    if name == "duckduckgo":
        try:
            hits = _ddg_lite_all_results(query, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.debug("DuckDuckGo multi-result error: %s", e)
            return []
        return [
            SearchResult(
                query=query,
                backend="duckduckgo",
                title=title,
                text=_truncate(snippet),
                source_url=url,
            )
            for title, snippet, url in hits
            if url
        ]

    if name == "tavily":
        try:
            return TavilyBackend(
                api_key=tavily_api_key,
                search_depth=tavily_search_depth,
                max_results=limit,
                timeout_s=tavily_timeout_s,
            ).search_all(query, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.debug("Tavily multi-result error: %s", e)
            return []

    # Single-result backends wrap their 0-or-1 result.
    one = search(query, backend=name)
    return [one] if one is not None else []

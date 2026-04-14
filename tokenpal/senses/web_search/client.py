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

BackendName = Literal["duckduckgo", "wikipedia", "brave"]

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


def _ddg_lite_first_result(query: str) -> tuple[str, str, str] | None:
    """Return (title, snippet, url) of the first DDG Lite result, or None."""
    # DDG Lite requires POST with form-urlencoded body; GET returns the search form.
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://lite.duckduckgo.com/lite/",
            data=data,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            body = resp.read(_MAX_HTML_BYTES).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        log.debug("DDG lite fetch failed: %s", e)
        return None

    if "result-link" not in body:
        return None

    link = _DDG_LITE_LINK_RE.search(body)
    snip = _DDG_LITE_SNIPPET_RE.search(body)
    if not link or not snip:
        return None

    title = _strip_html(link.group(2))
    snippet = _strip_html(snip.group(1))
    if not title or not snippet:
        return None

    # DDG Lite wraps outbound URLs in a redirect like /l/?uddg=<target>.
    # parse_qs already URL-decodes the value.
    href = link.group(1)
    uddg = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0]
    if uddg:
        href = uddg

    return title, snippet, href


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


def search(
    query: str,
    backend: str = "duckduckgo",
    brave_api_key: str = "",
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

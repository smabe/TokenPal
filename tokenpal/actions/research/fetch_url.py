"""fetch_url — pull a URL and extract clean article text.

Primary extractor: trafilatura (best F1 on news/long-form per academic
benchmarks). Fallback: readability-lxml. Neither executes JavaScript.
newspaper3k is abandoned upstream — if we ever need its style, the
newspaper4k fork is the live continuation; do not revive newspaper3k.

500KB raw-bytes cap before extraction, sensitive-term filter inside
``fetch_and_extract`` so both the LLM-tool path and the research
pipeline path share the same scrubbing. The action wraps the extracted
text in ``<tool_result>`` delimiters for the brain; the raw text is
what ResearchRunner consumes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar
from urllib.parse import urlparse

import aiohttp

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._http import _get_session
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.config.consent import Category, has_consent

log = logging.getLogger(__name__)

_MAX_BYTES = 2 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 8.0
# Descriptive UA required by a handful of endpoints (Wikimedia, TheSportsDB).
# Passed per-request so we don't override the shared session's global UA.
_FETCH_UA = (
    "TokenPal/1.0 (+https://github.com/smabe/TokenPal; "
    "abraham.awadallah@gmail.com)"
)
_MAX_EXTRACT_CHARS = 8000
# Extractions shorter than this are usually title-only dregs from a page
# trafilatura couldn't parse (heavy JS, paywall, anti-bot). Treat as
# failure so the research runner falls back to the search snippet, which
# at least carries the query terms instead of bare page boilerplate.
_MIN_EXTRACT_CHARS = 300


async def fetch_and_extract(url: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> str | None:
    """Fetch URL and return plain extracted article text. None on any failure,
    including sensitive-term detection. Callers are responsible for consent.

    Two-stage: try our aiohttp fetch + multi-extractor chain first. If the
    result is under _MIN_EXTRACT_CHARS, fall back to newspaper4k's own
    fetcher, which succeeds on sites that serve thin HTML to aiohttp
    (cnet, tomsguide, etc. do TLS fingerprinting or header-combo gates).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    text = await _fetch_via_newspaper(url, timeout_s=timeout_s)
    if not text or len(text) < _MIN_EXTRACT_CHARS:
        fallback = await _fetch_via_aiohttp(url, timeout_s=timeout_s)
        if fallback and len(fallback) > len(text):
            text = fallback

    if not text:
        return None
    if len(text) < _MIN_EXTRACT_CHARS:
        log.debug(
            "fetch_url: extraction too short (%d < %d chars) for %s",
            len(text), _MIN_EXTRACT_CHARS, url,
        )
        return None
    if contains_sensitive_content_term(text):
        log.debug("fetch_url: content filtered (sensitive) for %s", url)
        return None
    return text[:_MAX_EXTRACT_CHARS]


async def _fetch_via_aiohttp(url: str, *, timeout_s: float) -> str:
    try:
        raw = await asyncio.wait_for(_fetch(url), timeout=timeout_s)
    except TimeoutError:
        log.debug("fetch_url: aiohttp timeout after %.1fs for %s", timeout_s, url)
        return ""
    except aiohttp.ClientError as e:
        log.debug("fetch_url: aiohttp client error %s for %s", e, url)
        return ""
    if not raw:
        return ""
    text = _extract(raw, url)
    if not text:
        log.debug(
            "fetch_url: extraction empty (%d HTML bytes) for %s", len(raw), url
        )
    return text


async def _fetch_via_newspaper(url: str, *, timeout_s: float) -> str:
    """Run newspaper4k's own fetch+parse in a thread. Gets past TLS/header
    gates that reject aiohttp; returns "" on any failure."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_newspaper_blocking_fetch, url),
            timeout=timeout_s,
        )
    except TimeoutError:
        log.debug("fetch_url: newspaper timeout after %.1fs for %s", timeout_s, url)
        return ""


def _newspaper_blocking_fetch(url: str) -> str:
    try:
        import newspaper
    except ImportError:
        return ""
    try:
        article = newspaper.Article(url)
        article.download()
        article.parse()
        return (article.text or "").strip()
    except Exception as e:
        log.debug("newspaper fetch failed for %s: %s", url, e)
        return ""


@register_action
class FetchUrlAction(AbstractAction):
    action_name = "fetch_url"
    description = (
        "Fetch a URL and return the main article text stripped of boilerplate. "
        "No JavaScript, 500KB size cap, sensitive-term filter."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."},
        },
        "required": ["url"],
    }
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    safe: ClassVar[bool] = True
    requires_confirm: ClassVar[bool] = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        url = (kwargs.get("url") or "").strip()
        if not url:
            return ActionResult(output="fetch_url: empty URL", success=False)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ActionResult(output=f"fetch_url: bad URL '{url}'", success=False)
        if not has_consent(Category.WEB_FETCHES):
            return ActionResult(
                output="fetch_url: web_fetches consent not granted. Run /consent.",
                success=False,
            )

        text = await fetch_and_extract(url)
        if text is None:
            return ActionResult(
                output=f"fetch_url: nothing usable at {url}",
                success=False,
            )

        body = f"<tool_result tool=\"fetch_url\" url=\"{url}\">\n{text}\n</tool_result>"
        return ActionResult(output=body, success=True)


async def _fetch(url: str) -> str | None:
    session = await _get_session()
    async with session.get(url, allow_redirects=True, headers={"User-Agent": _FETCH_UA}) as resp:
        if resp.status >= 400:
            log.debug("fetch_url: HTTP %d from %s", resp.status, url)
            return None
        raw_bytes = await resp.content.read(_MAX_BYTES)
    if not raw_bytes:
        log.debug("fetch_url: zero-length body for %s", url)
        return None
    try:
        return raw_bytes.decode("utf-8", errors="replace")
    except LookupError:
        return None


def _extract(html: str, url: str) -> str:
    """Walk extractors in order; return the first result clearing the
    _MIN_EXTRACT_CHARS threshold, else the longest candidate.

    Different extractors win on different site shapes: trafilatura is best
    on clean news/long-form, newspaper4k is more aggressive on JS-heavy
    React/Next sites where trafilatura finds only title/boilerplate,
    readability is a last-resort tag-stripper. Running all three and
    picking the largest useful result beats betting on one.
    """
    candidates: list[str] = []
    for extractor in (_extract_trafilatura, _extract_newspaper, _extract_readability):
        result = extractor(html, url)
        if result and len(result) >= _MIN_EXTRACT_CHARS:
            return result
        if result:
            candidates.append(result)
    return max(candidates, key=len, default="")


def _extract_trafilatura(html: str, url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""
    modes: list[dict[str, Any]] = [
        {"favor_precision": True}, {"favor_recall": True}, {},
    ]
    for mode in modes:
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                **mode,
            )
        except Exception as e:
            log.debug("trafilatura extract failed for %s: %s", url, e)
            return ""
        if extracted:
            return str(extracted).strip()
    return ""


def _extract_newspaper(html: str, url: str) -> str:
    try:
        import newspaper
    except ImportError:
        return ""
    try:
        article = newspaper.Article(url)
        article.download(input_html=html)
        article.parse()
        return (article.text or "").strip()
    except Exception as e:
        log.debug("newspaper extract failed for %s: %s", url, e)
        return ""


def _extract_readability(html: str, url: str) -> str:
    try:
        from readability import Document
    except ImportError:
        log.warning("readability-lxml not installed; skipping last-resort extractor")
        return ""
    try:
        import re
        doc = Document(html)
        summary_html = doc.summary(html_partial=True)
        return re.sub(r"<[^>]+>", " ", summary_html).strip()
    except Exception as e:
        log.debug("readability extract failed for %s: %s", url, e)
        return ""

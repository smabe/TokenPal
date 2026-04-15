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
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.config.consent import Category, has_consent

log = logging.getLogger(__name__)

_MAX_BYTES = 500 * 1024
_DEFAULT_TIMEOUT_S = 8.0
# Descriptive UA required by a handful of endpoints (Wikimedia, TheSportsDB).
# Passed per-request so we don't override the shared session's global UA.
_FETCH_UA = (
    "TokenPal/1.0 (+https://github.com/smabe/TokenPal; "
    "abraham.awadallah@gmail.com)"
)
_MAX_EXTRACT_CHARS = 8000


async def fetch_and_extract(url: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> str | None:
    """Fetch URL and return plain extracted article text. None on any failure,
    including sensitive-term detection. Callers are responsible for consent."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    try:
        raw = await asyncio.wait_for(_fetch(url), timeout=timeout_s)
    except TimeoutError:
        return None
    except aiohttp.ClientError as e:
        log.debug("fetch_url: %s for %s", e, url)
        return None

    if not raw:
        return None

    text = _extract(raw, url)
    if not text:
        return None
    if contains_sensitive_term(text):
        log.debug("fetch_url: content filtered (sensitive) for %s", url)
        return None
    return text[:_MAX_EXTRACT_CHARS]


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
            return None
        raw_bytes = await resp.content.read(_MAX_BYTES)
    if not raw_bytes:
        return None
    try:
        return raw_bytes.decode("utf-8", errors="replace")
    except LookupError:
        return None


def _extract(html: str, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted:
            return str(extracted).strip()
    except ImportError:
        log.debug("trafilatura not installed; trying readability-lxml")
    except Exception:
        log.exception("trafilatura extract failed for %s", url)

    try:
        from readability import Document

        doc = Document(html)
        summary_html = doc.summary(html_partial=True)
        import re

        return re.sub(r"<[^>]+>", " ", summary_html).strip()
    except ImportError:
        log.warning("readability-lxml not installed; returning empty extraction")
        return ""
    except Exception:
        log.exception("readability extract failed for %s", url)
        return ""

"""fetch_url — pull a URL and extract clean article text.

Primary extractor: trafilatura (best F1 on news/long-form per academic
benchmarks). Fallback: readability-lxml. Neither executes JavaScript.
newspaper3k is abandoned upstream — if we ever need its style, the
newspaper4k fork is the live continuation; do not revive newspaper3k.

500KB raw-bytes cap before extraction, sensitive-term filter on the
extracted text before returning. Result wrapped in <tool_result> delimiters
so the brain treats it as untrusted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar
from urllib.parse import urlparse

import aiohttp

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.config.consent import Category, has_consent

log = logging.getLogger(__name__)

_MAX_BYTES = 500 * 1024
_DEFAULT_TIMEOUT_S = 8.0
_USER_AGENT = (
    "TokenPal/1.0 (+https://github.com/smabe/TokenPal; "
    "abraham.awadallah@gmail.com)"
)
# Cap on returned extracted text so one call can't blow the caller's context.
_MAX_EXTRACT_CHARS = 8000


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

        try:
            raw = await asyncio.wait_for(_fetch(url), timeout=_DEFAULT_TIMEOUT_S)
        except TimeoutError:
            return ActionResult(output=f"fetch_url: timed out for {url}", success=False)
        except aiohttp.ClientError as e:
            return ActionResult(output=f"fetch_url: {e}", success=False)

        if raw is None:
            return ActionResult(output=f"fetch_url: empty body for {url}", success=False)

        text = _extract(raw, url)
        if not text:
            return ActionResult(
                output=f"fetch_url: no readable content at {url}",
                success=False,
            )

        if contains_sensitive_term(text):
            log.debug("fetch_url: content filtered (sensitive) for %s", url)
            return ActionResult(
                output=f"fetch_url: filtered content at {url}",
                success=False,
            )

        trimmed = text[:_MAX_EXTRACT_CHARS]
        body = f"<tool_result tool=\"fetch_url\" url=\"{url}\">\n{trimmed}\n</tool_result>"
        return ActionResult(output=body, success=True)


async def _fetch(url: str) -> str | None:
    headers = {"User-Agent": _USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
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
    """Try trafilatura first; fall back to readability-lxml. Returns plain text."""
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

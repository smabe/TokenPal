"""Shared aiohttp plumbing + result wrapping for phase 2b tools.

One lazily-initialized ``aiohttp.ClientSession`` process-wide so the tools
share connection pooling. Every external response is run through
``contains_sensitive_term`` and wrapped in ``<tool_result>`` delimiters
before the brain ever sees it — same pattern as ``/ask`` and the
world_awareness sense.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from typing import Any

import aiohttp

from tokenpal.brain.personality import contains_sensitive_term

log = logging.getLogger(__name__)

_USER_AGENT = "TokenPal/1.0 (github.com/smabe/TokenPal)"
_DEFAULT_TIMEOUT_S = 10.0
_SENSITIVE_PLACEHOLDER = "[filtered]"

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(headers={"User-Agent": _USER_AGENT})
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _atexit_close() -> None:
    if _session is None or _session.closed:
        return
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(close_session())
        finally:
            loop.close()
    except Exception as exc:
        log.debug("session close at exit failed: %s", exc)


atexit.register(_atexit_close)


async def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
    retries: int = 1,
) -> tuple[Any, str | None]:
    """Fetch a URL and JSON-parse the response.

    Returns ``(data, None)`` on success or ``(None, error_message)`` on
    failure. Never raises. Retries once on transient network errors.
    """
    session = await _get_session()
    last_err: str | None = None
    attempts = max(1, retries + 1)
    for _ in range(attempts):
        try:
            async with asyncio.timeout(timeout):
                async with session.get(url, headers=headers or {}) as resp:
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}"
                        continue
                    return await resp.json(content_type=None), None
        except (aiohttp.ClientError, TimeoutError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        except ValueError as exc:
            last_err = f"json parse failed: {exc}"
            break
    return None, last_err or "unknown fetch failure"


async def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
    retries: int = 1,
) -> tuple[str | None, str | None]:
    """Same as ``fetch_json`` but returns raw text."""
    session = await _get_session()
    last_err: str | None = None
    attempts = max(1, retries + 1)
    for _ in range(attempts):
        try:
            async with asyncio.timeout(timeout):
                async with session.get(url, headers=headers or {}) as resp:
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}"
                        continue
                    return await resp.text(), None
        except (aiohttp.ClientError, TimeoutError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
    return None, last_err or "unknown fetch failure"


def _scrub_line(line: str) -> str:
    return _SENSITIVE_PLACEHOLDER if contains_sensitive_term(line) else line


def wrap_result(tool_name: str, body: str) -> str:
    """Wrap ``body`` in a ``<tool_result>`` envelope after sensitive-term scrubbing.

    The scrub is line-wise so one bad token doesn't nuke the whole response.
    """
    safe_lines = [_scrub_line(line) for line in body.splitlines() or [body]]
    filtered = "\n".join(safe_lines)
    return f'<tool_result tool="{tool_name}">\n{filtered}\n</tool_result>'

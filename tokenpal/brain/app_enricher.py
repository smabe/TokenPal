"""Fetch one-line descriptions of unfamiliar apps for richer context.

First time we see an app name we don't have cached, we block the
observation tick on a `search()` call (3s timeout), grab the first
sentence, and cache it in memory.db. Subsequent ticks for the same
app are instant cache hits. Cache is 30d; failures retry after 24h.

Privacy posture matches /ask: consent-gated, sensitive-term filtered,
sensitive apps never touch the network.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from tokenpal.brain.personality import (
    contains_sensitive_content_term,
    contains_sensitive_term,
)
from tokenpal.config.consent import Category, has_consent
from tokenpal.senses.web_search.client import search

if TYPE_CHECKING:
    from tokenpal.brain.memory import MemoryStore

log = logging.getLogger(__name__)

REFRESH_AFTER_S = 30 * 24 * 3600
RETRY_AFTER_S = 24 * 3600
FETCH_TIMEOUT_S = 3.0
MAX_DESCRIPTION_CHARS = 120

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Platform-internal processes that aren't worth enriching.
NON_APP_NAMES: frozenset[str] = frozenset({
    "finder", "loginwindow", "windowserver", "systemuiserver",
    "dock", "controlcenter", "notificationcenter", "spotlight",
    "universalcontrol", "quicklookuihelper", "screensaverengine",
    "coreservicesuiagent", "accessibility", "siri",
    "explorer.exe", "dwm.exe", "svchost.exe", "csrss.exe",
    "lockapp.exe", "searchui.exe", "shellexperiencehost.exe",
    "xorg", "gnome-shell", "kwin_x11", "plasmashell",
})


def _trim_to_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    pieces = _SENTENCE_SPLIT.split(text, maxsplit=1)
    first = pieces[0].strip()
    if len(first) > MAX_DESCRIPTION_CHARS:
        first = first[:MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return first


class AppEnricher:
    """Looks up one-line descriptions for unfamiliar app names."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._in_flight: dict[str, asyncio.Task[str | None]] = {}
        # Per-session cache keyed by app name → description (or None for
        # known-gated/failed). Short-circuits the SQLite + gating path
        # after first encounter, which is the common case (30+ ticks per
        # app per session).
        self._session_cache: dict[str, str | None] = {}

    def _is_gated(self, app_name: str) -> bool:
        name = app_name.strip()
        if not name:
            return True
        lower = name.lower()
        if lower in NON_APP_NAMES:
            return True
        # contains_sensitive_term already walks _SENSITIVE_APPS_LOWER,
        # so there's no second list to keep in sync here.
        if contains_sensitive_term(name):
            return True
        return False

    async def enrich(self, app_name: str) -> str | None:
        """Return a cached or freshly-fetched one-line description."""
        if app_name in self._session_cache:
            return self._session_cache[app_name]

        if self._is_gated(app_name):
            self._session_cache[app_name] = None
            return None
        if not self._memory.enabled:
            return None

        cached = self._memory.get_app_enrichment(
            app_name,
            fresh_after_s=REFRESH_AFTER_S,
            retry_after_s=RETRY_AFTER_S,
        )
        if cached is not None:
            description, still_fresh = cached
            if still_fresh:
                self._session_cache[app_name] = description
                return description

        if not has_consent(Category.WEB_FETCHES):
            return None

        if app_name in self._in_flight:
            return await self._in_flight[app_name]

        task = asyncio.create_task(self._fetch(app_name))
        self._in_flight[app_name] = task
        try:
            description = await task
            self._session_cache[app_name] = description
            return description
        finally:
            self._in_flight.pop(app_name, None)

    async def _fetch(self, app_name: str) -> str | None:
        query = f"{app_name} software"
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(search, query),
                timeout=FETCH_TIMEOUT_S,
            )
        except TimeoutError:
            log.debug("AppEnricher: timeout fetching %s", app_name)
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None
        except Exception:  # noqa: BLE001 — network path must never raise
            log.debug("AppEnricher: error fetching %s", app_name, exc_info=True)
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        if result is None:
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        combined = f"{result.title}\n{result.text}"
        if contains_sensitive_content_term(combined):
            log.debug("AppEnricher: sensitive term in result for %s", app_name)
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        description = _trim_to_sentence(result.text or result.title or "")
        if not description:
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        self._memory.put_app_enrichment(app_name, description, success=True)
        log.info("AppEnricher: cached %r → %s", app_name, description)
        return description

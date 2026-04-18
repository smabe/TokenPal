"""Fetch one-line descriptions of unfamiliar apps for richer context.

First time we see an app name we don't have cached, we block the
observation tick on a search_web call (3s timeout), grab the first
sentence, and cache it in memory.db. Subsequent ticks for the same
app are instant cache hits. Cache is 30d; failures retry after 24h.

Privacy posture matches search_web: consent-gated, sensitive-term
filtered, sensitive apps never touch the network.
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

REFRESH_AFTER_S = 30 * 24 * 3600   # 30 days
RETRY_AFTER_S = 24 * 3600          # 24 hours for failed lookups
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

    def __init__(
        self,
        memory: MemoryStore,
        sensitive_apps: set[str] | frozenset[str],
    ) -> None:
        self._memory = memory
        self._sensitive = {a.lower() for a in sensitive_apps}
        self._in_flight: dict[str, asyncio.Task[str | None]] = {}

    def _is_gated(self, app_name: str) -> bool:
        name = app_name.strip()
        if not name:
            return True
        if name.lower() in NON_APP_NAMES:
            return True
        if name.lower() in self._sensitive:
            return True
        if contains_sensitive_term(name):
            return True
        return False

    async def enrich(self, app_name: str) -> str | None:
        """Return a cached or freshly-fetched one-line description."""
        if self._is_gated(app_name):
            return None
        if not self._memory.enabled:
            return None

        cached = self._memory.get_app_enrichment(app_name)
        if cached is not None:
            description, age_s, success = cached
            if success and age_s < REFRESH_AFTER_S:
                return description
            if not success and age_s < RETRY_AFTER_S:
                return None
            # Otherwise fall through and re-fetch.

        if not has_consent(Category.WEB_FETCHES):
            return None

        if app_name in self._in_flight:
            return await self._in_flight[app_name]

        task = asyncio.create_task(self._fetch(app_name))
        self._in_flight[app_name] = task
        try:
            return await task
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

        if (
            contains_sensitive_content_term(result.text)
            or contains_sensitive_content_term(result.title)
        ):
            log.debug("AppEnricher: sensitive term in result for %s", app_name)
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        description = _trim_to_sentence(result.text or result.title or "")
        if not description:
            self._memory.put_app_enrichment(app_name, None, success=False)
            return None

        self._memory.put_app_enrichment(app_name, description, success=True)
        log.info("AppEnricher: cached %r → %s", app_name, description[:60])
        return description

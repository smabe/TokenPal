"""Per-sense observation-snapshot enrichment.

Generalizes the existing `AppEnricher` pattern to additional sense
transitions. The brain asks the enricher to splice a one-line
description into the current snapshot before prompt composition. Every
handler shares the AppEnricher latency posture: 3s cap on the fetch,
`memory.db` cache for 30 days, silent retry-backoff on failure,
consent-gated for the network-backed flavors.

Two handlers ship today:

- ``app_awareness``: splices the foreground app's description into the
  ``App: <name>`` line (migrated verbatim from the old
  ``_maybe_enrich_snapshot``).
- ``process_heat``: when the sense names a top CPU hog, looks up a
  one-liner for the process and appends it to that sense's summary.

Add a new handler by writing a short helper that returns a
``(pattern, replacement)`` tuple for ``str.replace`` and wiring it into
``enrich`` below — see ``_enrich_app_awareness`` for the template.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tokenpal.brain.app_enricher import AppEnricher

log = logging.getLogger(__name__)


class ObservationEnricher:
    """Runs sense-specific handlers against a fresh snapshot, returns it."""

    def __init__(self, *, app_enricher: AppEnricher) -> None:
        self._app_enricher = app_enricher

    async def enrich(
        self,
        snapshot: str,
        readings: dict[str, Any],
    ) -> str:
        """Rewrite the snapshot with any enrichments that can fire now.

        Each handler is a no-op when its reading is missing or when its
        gating checks (sensitive app, unknown name, network failure) say
        not to enrich. Runs handlers sequentially — the snapshot is
        rewritten in place so later handlers see the enriched form.
        """
        snapshot = await self._enrich_app_awareness(snapshot, readings)
        snapshot = await self._enrich_process_heat(snapshot, readings)
        return snapshot

    async def _enrich_app_awareness(
        self, snapshot: str, readings: dict[str, Any],
    ) -> str:
        """Splice the foreground app's description into `App: <name>`.

        Migrated from Brain._maybe_enrich_snapshot. Behavior is identical:
        the AppEnricher owns consent gating, sensitive-app skipping, and
        the 3s blocking-fetch cap for first-encounter apps.
        """
        reading = readings.get("app_awareness")
        if reading is None:
            return snapshot
        data = getattr(reading, "data", None) or {}
        app_name = data.get("app_name") if isinstance(data, dict) else None
        if not app_name:
            return snapshot
        description = await self._app_enricher.enrich(app_name)
        if not description:
            return snapshot
        return snapshot.replace(
            f"App: {app_name}", f"App: {app_name} ({description})", 1,
        )

    async def _enrich_process_heat(
        self, snapshot: str, readings: dict[str, Any],
    ) -> str:
        """Append the top CPU hog's description to its reading summary.

        Reuses AppEnricher's cache + consent pipeline — process names
        look enough like app names that a single enrichment table is
        fine. The sensitive-app filter in AppEnricher stops us from
        enriching anything that would leak context.
        """
        reading = readings.get("process_heat")
        if reading is None:
            return snapshot
        data = getattr(reading, "data", None) or {}
        process = data.get("top_process") if isinstance(data, dict) else None
        if not process:
            return snapshot
        summary = getattr(reading, "summary", "")
        if not isinstance(summary, str) or not summary or summary not in snapshot:
            return snapshot
        description = await self._app_enricher.enrich(process)
        if not description:
            return snapshot
        enriched_summary = f"{summary} — {process} is {description}"
        return snapshot.replace(summary, enriched_summary, 1)

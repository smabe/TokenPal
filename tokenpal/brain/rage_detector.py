"""Frustration / rage pattern detection.

Fires when the user's activity matches the shape of "hit a wall and bailed":
 1. Recent typing burst at rapid/furious pace (via typing_cadence sense).
 2. Typing then drops out of the fast tier for at least ``rage_post_pause_min_s``
    and no more than ``rage_post_pause_max_s`` seconds.
 3. During that post-burst pause, the user switches to a known distraction
    app (configurable list).

Design notes (see plans/buddy-utility-wedges.md):
 * Consumes SenseReading objects only. NEVER imports or touches
   ``_keyboard_bus`` — keystrokes are not observed by this module. A source
   grep test enforces this invariant.
 * Default disabled via RageDetectConfig.enabled so normal typing flows
   don't produce false-positive nudges.
 * Per-session cooldown (``cooldown_s``) is enforced internally.
 * High-signal path: orchestrator emits the nudge via the ``changed_from``
   bypass so it doesn't count against the 8/5min observation cap, consistent
   with the git-sense pattern.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from tokenpal.config.schema import RageDetectConfig
from tokenpal.senses.base import SenseReading

log = logging.getLogger(__name__)

_FAST_BUCKETS: frozenset[str] = frozenset({"rapid", "furious"})


@dataclass(frozen=True)
class RageSignal:
    """A rage-detect trigger."""

    app_name: str
    pause_s: float


class RageDetector:
    """Stateful matcher over typing_cadence + app_awareness readings."""

    def __init__(self, config: RageDetectConfig) -> None:
        self._config = config
        # Monotonic times.
        self._rapid_ended_at: float | None = None
        self._last_rapid_at: float | None = None
        self._last_emit_at: float = 0.0
        self._current_bucket: str = "idle"

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def ingest(self, readings: list[SenseReading]) -> RageSignal | None:
        """Feed this tick's readings through the matcher.

        Returns a RageSignal when the pattern fires, else None.  The caller
        is responsible for calling ``mark_emitted()`` when it actually emits
        a nudge so the cooldown starts from that point.
        """
        if not self._config.enabled:
            return None

        now = time.monotonic()

        # Walk typing readings first so the distraction check sees fresh
        # rapid/pause state from the same tick.
        for r in readings:
            if r.sense_name == "typing_cadence":
                self._on_typing(r, now)

        for r in readings:
            if r.sense_name == "app_awareness":
                signal = self._on_app(r, now)
                if signal is not None:
                    return signal
        return None

    def mark_emitted(self) -> None:
        self._last_emit_at = time.monotonic()
        # Arm the detector for a fresh pattern next time.
        self._rapid_ended_at = None

    # ------------------------------------------------------------------

    def _on_typing(self, reading: SenseReading, now: float) -> None:
        bucket = (reading.data or {}).get("bucket")
        if not isinstance(bucket, str):
            return
        prev = self._current_bucket
        self._current_bucket = bucket
        if bucket in _FAST_BUCKETS:
            self._last_rapid_at = now
            # Re-entering fast clears any stale pause timestamp.
            self._rapid_ended_at = None
        elif prev in _FAST_BUCKETS and bucket not in _FAST_BUCKETS:
            self._rapid_ended_at = now

    def _on_app(self, reading: SenseReading, now: float) -> RageSignal | None:
        # Only fire on transitions, not every steady-state poll.
        if not reading.changed_from:
            return None
        app_name = (reading.data or {}).get("app_name")
        if not isinstance(app_name, str) or not app_name:
            return None
        if not self._is_distraction(app_name):
            return None
        if self._rapid_ended_at is None:
            return None
        pause_s = now - self._rapid_ended_at
        if pause_s < self._config.rage_post_pause_min_s:
            return None
        if pause_s > self._config.rage_post_pause_max_s:
            # Pause too long — this isn't a rage-quit, it's just normal idle.
            return None
        # Must be a recent burst, not one from hours ago.
        if (
            self._last_rapid_at is None
            or (now - self._last_rapid_at) > self._config.rage_burst_recency_s
        ):
            return None
        if (now - self._last_emit_at) < self._config.cooldown_s:
            return None
        log.info(
            "Rage pattern: %s switch after %.0fs pause post-burst",
            app_name,
            pause_s,
        )
        return RageSignal(app_name=app_name, pause_s=pause_s)

    def _is_distraction(self, app_name: str) -> bool:
        lower = app_name.lower()
        return any(d.lower() in lower for d in self._config.distraction_apps)

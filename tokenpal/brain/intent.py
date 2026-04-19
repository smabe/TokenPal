"""Ambient-goal tracking + drift detection.

The user sets an intent via `/intent <text>` ("finish auth PR", "read through
the migration docs", ...). The buddy doesn't check whether current activity
*matches* the intent — that's too noisy and wrong often enough to be
annoying. Instead it watches for a narrower failure mode: drift into a known
distraction app for long enough that the user probably lost the thread.

Drift triggers when ALL of:
 * An active intent exists (within ``max_age_s``).
 * The current app is in ``distraction_apps`` (case-insensitive substring).
 * The current app has been in focus for at least ``drift_min_dwell_s``.

The detector is side-effect-free: it answers "is the user drifting right
now?" and lets the orchestrator decide when/how to emit.

See plans/buddy-utility-wedges.md.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.config.schema import IntentConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveIntent:
    text: str
    started_at: float
    session_id: str


@dataclass(frozen=True)
class DriftSignal:
    """A drift trigger — the orchestrator decides whether to actually emit."""

    intent_text: str
    app_name: str
    dwell_s: float


class IntentError(ValueError):
    """Raised when an intent write is refused (e.g. sensitive-term match)."""


class IntentStore:
    """Facade over MemoryStore + IntentConfig for intent tracking.

    Not thread-safe by itself — relies on MemoryStore's internal lock for
    DB writes. App-dwell tracking is called from the async brain loop only.
    """

    def __init__(self, memory: MemoryStore, config: IntentConfig) -> None:
        self._memory = memory
        self._config = config
        # Dwell tracker — the app currently in focus and the monotonic time
        # it became current. Reset on every app change via on_app_change().
        self._current_app: str = ""
        self._current_app_since: float = 0.0
        # Last drift nudge (monotonic) — enforces drift_cooldown_s.
        self._last_drift_emit: float = 0.0

    # ------------------------------------------------------------------
    # Intent CRUD
    # ------------------------------------------------------------------

    def set(self, text: str) -> ActiveIntent:
        """Upsert the active intent. Raises IntentError on sensitive content."""
        cleaned = text.strip()
        if not cleaned:
            raise IntentError("Intent text cannot be empty.")
        if contains_sensitive_term(cleaned):
            raise IntentError(
                "Intent text looks like it references a sensitive app; "
                "not stored."
            )
        self._memory.set_active_intent(cleaned)
        row = self._memory.get_active_intent()
        assert row is not None, "set_active_intent followed by get_active_intent returned None"
        text_, started_at, session_id = row
        log.info("Intent set: %r (session %s)", text_, session_id)
        return ActiveIntent(text=text_, started_at=started_at, session_id=session_id)

    def clear(self) -> bool:
        """Remove the active intent. Returns True if anything was cleared."""
        existed = self._memory.get_active_intent() is not None
        self._memory.clear_active_intent()
        if existed:
            log.info("Intent cleared")
        return existed

    def get_active(self) -> ActiveIntent | None:
        """Return the currently-active intent or None.

        Intents older than ``max_age_s`` are treated as expired — they're
        not auto-deleted, just ignored for drift checks.  ``/intent status``
        sees them via ``get_raw()`` if you want to display "expired".
        """
        row = self._memory.get_active_intent()
        if row is None:
            return None
        text, started_at, session_id = row
        age_s = time.time() - started_at
        if age_s > self._config.max_age_s:
            log.debug(
                "Intent expired (age %.0fh > max %.0fh); ignoring",
                age_s / 3600,
                self._config.max_age_s / 3600,
            )
            return None
        return ActiveIntent(text=text, started_at=started_at, session_id=session_id)

    def get_raw(self) -> ActiveIntent | None:
        """Return the active intent row regardless of age."""
        row = self._memory.get_active_intent()
        if row is None:
            return None
        text, started_at, session_id = row
        return ActiveIntent(text=text, started_at=started_at, session_id=session_id)

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def on_app_change(self, app_name: str) -> None:
        """Notify the store that the focused app changed.

        Resets the dwell timer. Called from the orchestrator when an
        app_awareness reading's ``changed_from`` fires.
        """
        if app_name == self._current_app:
            return
        self._current_app = app_name
        self._current_app_since = time.monotonic()

    def check_drift(self) -> DriftSignal | None:
        """If drift conditions are met, return a DriftSignal; else None.

        Respects the drift cooldown internally — subsequent calls within
        ``drift_cooldown_s`` of the last emission return None.
        Caller must invoke ``mark_drift_emitted()`` when it actually fires
        a nudge so the cooldown starts from the emit time, not the check.
        """
        intent = self.get_active()
        if intent is None:
            return None
        if not self._current_app:
            return None
        now = time.monotonic()
        if (now - self._last_drift_emit) < self._config.drift_cooldown_s:
            return None
        if not self._is_distraction(self._current_app):
            return None
        dwell_s = now - self._current_app_since
        if dwell_s < self._config.drift_min_dwell_s:
            return None
        return DriftSignal(
            intent_text=intent.text,
            app_name=self._current_app,
            dwell_s=dwell_s,
        )

    def mark_drift_emitted(self) -> None:
        """Start the drift cooldown from now."""
        self._last_drift_emit = time.monotonic()

    def _is_distraction(self, app_name: str) -> bool:
        lower = app_name.lower()
        return any(d.lower() in lower for d in self._config.distraction_apps)

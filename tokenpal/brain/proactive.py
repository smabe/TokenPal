"""Proactive scheduler — drives opt-in recurring nudges (stretch, water, etc).

Design rules (see plans/pal-improvement-grand-plan.md, phase 3):
- Nudges surface through the brain's `ui_callback` as speech bubbles, not
  OS notifications.
- Nudges pause while a conversation session is active.
- Nudges pause while a sensitive app is in the foreground.
- State is in-memory; restart clears any enrolled nudges.

The scheduler is intentionally dumb: it owns a list of (name, interval,
last_fired, message_fn) tuples and fires the message_fn on tick() when
the interval elapses and the gates are open. Callers (actions) enroll
themselves by calling `register()` and pass a callable that returns the
speech-bubble text. Unregister via `cancel()`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MessageFn = Callable[[], str]
GateFn = Callable[[], bool]


@dataclass
class ScheduledNudge:
    name: str
    interval_s: float
    message_fn: MessageFn
    last_fired_at: float = field(default_factory=time.monotonic)
    # Optional per-nudge "expire this nudge once it fires successfully N
    # times at a certain wall-clock"; bedtime_wind_down uses one-shot
    # semantics via a message_fn that returns "" after it's past the
    # target time. Empty message means "skip, not ready".
    one_shot: bool = False


class ProactiveScheduler:
    """Ticked from the brain loop. Fires due nudges through ui_callback.

    Parameters
    ----------
    ui_callback
        How a nudge reaches the user. Must behave like `brain._ui_callback`
        (post speech bubble).
    is_paused
        Returns True when no nudge should fire. The brain wires this to
        `conversation.is_active OR sensitive_app_in_foreground`.
    """

    def __init__(
        self,
        ui_callback: Callable[[str], None],
        is_paused: GateFn,
    ) -> None:
        self._ui_callback = ui_callback
        self._is_paused = is_paused
        self._nudges: dict[str, ScheduledNudge] = {}

    # ------------------------------------------------------------------
    # enrollment
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        interval_s: float,
        message_fn: MessageFn,
        one_shot: bool = False,
    ) -> None:
        """Enroll or replace a nudge. Interval is seconds between fires."""
        if interval_s <= 0:
            raise ValueError(f"interval_s must be positive, got {interval_s}")
        self._nudges[name] = ScheduledNudge(
            name=name,
            interval_s=interval_s,
            message_fn=message_fn,
            one_shot=one_shot,
        )
        log.info("Proactive nudge '%s' registered (every %.0fs)", name, interval_s)

    def cancel(self, name: str) -> bool:
        """Remove a nudge. Returns True if one was removed."""
        existed = self._nudges.pop(name, None) is not None
        if existed:
            log.info("Proactive nudge '%s' cancelled", name)
        return existed

    def is_registered(self, name: str) -> bool:
        return name in self._nudges

    def registered_names(self) -> list[str]:
        return list(self._nudges)

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[str]:
        """Fire any nudges whose interval has elapsed and whose gate is open.

        Returns the names of nudges that fired (useful for tests).
        """
        if not self._nudges:
            return []
        if self._is_paused():
            # Don't advance last_fired_at — the nudge should fire as soon
            # as the gate reopens, not be deferred by a full interval.
            return []

        now = now if now is not None else time.monotonic()
        fired: list[str] = []
        to_remove: list[str] = []

        for nudge in list(self._nudges.values()):
            if now - nudge.last_fired_at < nudge.interval_s:
                continue
            try:
                text = nudge.message_fn()
            except Exception:
                log.exception("Proactive nudge '%s' message_fn raised", nudge.name)
                nudge.last_fired_at = now
                continue
            if not text:
                # message_fn chose silence (e.g. bedtime not yet reached);
                # don't reset the clock so it tries again next tick.
                continue
            self._ui_callback(text)
            nudge.last_fired_at = now
            fired.append(nudge.name)
            if nudge.one_shot:
                to_remove.append(nudge.name)

        for name in to_remove:
            self._nudges.pop(name, None)

        return fired

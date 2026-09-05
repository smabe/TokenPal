"""Proactive scheduler — drives opt-in recurring nudges (stretch, water, etc).

Design rules (see plans/proactive-nudges.md):
- Nudges surface through the brain's `ui_callback` as speech bubbles, not
  OS notifications.
- Nudges pause while the brain is paused, a long task is running, a
  conversation is active, or a sensitive app is in the foreground.
- The scheduler owns the schedule. A nudge carries a serialisable
  `Schedule`, never a closure, which is what lets armed state survive a
  restart through `memory.db`.
- Everything is wall clock (`time.time()`): `time.monotonic()` excludes
  system sleep on macOS, so a nudge due during a lid-close would never come
  due at all.
- A deadline missed while the machine was asleep or closed fires ONCE on
  wake and re-arms from `now`, never a backlog of one fire per missed
  interval.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.schedule import Schedule

log = logging.getLogger(__name__)

# The longest gap any schedule can produce: a 24 h interval, or a daily fire
# across a 25 h DST day, plus slack.
_MAX_DEADLINE_AHEAD_S = 26 * 3600.0

# The speech bubble replaces rather than queues and lingers for
# _BUBBLE_HIDE_DELAY_MS (15 s, ui/qt/overlay.py). Two nudges closer together
# than that and the user only ever reads the second, so consecutive fires are
# spaced instead -- the loop ticks every 2 s, which is far too fast on its own.
_MIN_NUDGE_GAP_S = 16.0

GateFn = Callable[[], bool]


@dataclass
class ScheduledNudge:
    """One armed reminder: what to say, when, and when it last said it."""

    id: str
    label: str
    schedule: Schedule
    next_due_at: float
    last_fired_at: float | None = None


class ProactiveScheduler:
    """Ticked from the brain loop. Fires due nudges through ui_callback.

    Parameters
    ----------
    ui_callback
        How a nudge reaches the user. Must behave like `brain._ui_callback`
        (post speech bubble).
    is_paused
        Returns True when no nudge should fire. The brain wires this to
        `_proactive_paused`, which covers the paused flag, a running long
        task, an active conversation and a sensitive foreground app.
    memory
        Where armed state lives. `register` / `cancel` / `tick` write through
        to it; with None (or a disabled store) the scheduler is purely
        in-memory and nothing survives a restart.
    """

    def __init__(
        self,
        ui_callback: Callable[[str], None],
        is_paused: GateFn,
        memory: MemoryStore | None = None,
    ) -> None:
        self._ui_callback = ui_callback
        self._is_paused = is_paused
        self._memory = memory
        self._nudges: dict[str, ScheduledNudge] = {}
        self._last_emit_at = 0.0

    # ------------------------------------------------------------------
    # enrollment
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        id: str,
        label: str,
        schedule: Schedule,
        next_due_at: float | None = None,
    ) -> None:
        """Arm or re-arm a nudge, writing the row through to memory.db.

        `next_due_at` defaults to the schedule's next occurrence after now.
        A re-arm keeps the existing `last_fired_at` — changing a reminder's
        text or schedule must not erase the record that it fired.
        """
        now = time.time()
        due = next_due_at if next_due_at is not None else schedule.next_due_at(now)
        existing = self._nudges.get(id)
        self._nudges[id] = ScheduledNudge(
            id=id,
            label=label,
            schedule=schedule,
            next_due_at=due,
            last_fired_at=existing.last_fired_at if existing is not None else None,
        )
        if self._memory is not None:
            self._memory.upsert_reminder(id, label, schedule.to_row(), due)
        log.info("Proactive nudge '%s' armed (%s), next due in %.0fs",
                 id, schedule.kind, due - now)

    def cancel(self, id: str) -> bool:
        """Disarm a nudge and unpersist it. True if one was armed."""
        existed = self._nudges.pop(id, None) is not None
        if self._memory is not None:
            existed = self._memory.delete_reminder(id) or existed
        if existed:
            log.info("Proactive nudge '%s' cancelled", id)
        return existed

    def armed(self) -> list[ScheduledNudge]:
        """Every armed nudge, soonest deadline first."""
        return sorted(self._nudges.values(), key=lambda n: n.next_due_at)

    def hydrate(self) -> int:
        """Load armed reminders from memory.db. Returns how many loaded.

        Called once at brain start, never per tick — MemoryStore blocks on a
        lock the Qt thread also takes. A row whose schedule columns cannot be
        parsed is skipped and logged: a hand-edited or future-version
        database must not stop the brain from starting.
        """
        if self._memory is None:
            return 0
        loaded = 0
        now = time.time()
        for row in self._memory.list_reminders():
            reminder_id = str(row.get("id", ""))
            try:
                schedule = Schedule.from_row(row)
                next_due_at = float(row["next_due_at"])
            except (ValueError, TypeError, KeyError):
                log.warning("Skipping unreadable reminder row '%s'", reminder_id)
                continue
            label = str(row.get("label", ""))
            # A clock that jumped forward and was corrected leaves a deadline
            # no schedule could produce. Repair it on disk too: an in-memory
            # fix alone re-clamps every launch, and a session shorter than the
            # recomputed gap would never reach the fire.
            if next_due_at > now + _MAX_DEADLINE_AHEAD_S:
                next_due_at = schedule.next_due_at(now)
                self._memory.upsert_reminder(
                    reminder_id, label, schedule.to_row(), next_due_at
                )
                log.warning(
                    "Reminder '%s' had an impossible deadline; re-armed", reminder_id
                )
            last_fired_at = row.get("last_fired_at")
            self._nudges[reminder_id] = ScheduledNudge(
                id=reminder_id,
                label=label,
                schedule=schedule,
                # A deadline already in the past is kept as-is: the next tick
                # gives it its one fire and re-arms from then.
                next_due_at=next_due_at,
                last_fired_at=None if last_fired_at is None else float(last_fired_at),
            )
            loaded += 1
        if loaded:
            log.info("Hydrated %d armed reminder(s) from memory", loaded)
        return loaded

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[ScheduledNudge]:
        """Fire the soonest due nudge whose gate is open. Returns what fired.

        At most one, and never within _MIN_NUDGE_GAP_S of the last: the bubble
        replaces rather than queues, so several overdue nudges delivered back
        to back leave the user reading only the last. Every relaunch after a
        long absence is that case. The rest stay due and follow on later ticks.

        The list return holds at most one element. It is a list so p5 can spawn
        a generation task per fired nudge without the scheduler importing
        asyncio.

        The new deadline is computed from `now`, not from the deadline that
        was missed, so a five-hour gap yields one fire and one future
        deadline instead of a backlog.
        """
        if not self._nudges:
            return []
        if self._is_paused():
            # Don't advance the deadline — the nudge should fire as soon as
            # the gate reopens, not be deferred by a full interval.
            return []

        now = now if now is not None else time.time()
        if now - self._last_emit_at < _MIN_NUDGE_GAP_S:
            return []

        due = sorted(
            (n for n in self._nudges.values() if now >= n.next_due_at),
            key=lambda n: n.next_due_at,
        )
        for nudge in due:
            next_due_at = nudge.schedule.next_due_at(now)
            if self._memory is not None and not self._memory.mark_reminder_fired(
                nudge.id, now, next_due_at
            ):
                # The row was disarmed between arming and this tick; the
                # in-memory copy is backed by nothing, so drop it.
                log.info("Proactive nudge '%s' no longer persisted; dropping", nudge.id)
                self._nudges.pop(nudge.id, None)
                continue
            nudge.last_fired_at = now
            nudge.next_due_at = next_due_at
            self._last_emit_at = now
            try:
                self._ui_callback(nudge.label)
            except Exception:
                # Delivery must not abort the brain-loop iteration that called
                # tick(); the nudge is already marked fired either way.
                log.exception("Proactive nudge '%s' delivery raised", nudge.id)
            return [nudge]

        return []

"""Serialisable schedule for proactive reminders.

Two kinds, one type: a fixed interval, and a daily local time of day.  All
timestamps are wall-clock ``time.time()`` epoch seconds — ``time.monotonic()``
excludes system sleep on macOS and its epoch is per-boot, so it cannot survive
a restart.

Parsing and validation of the tool's arguments live here and nowhere else.
Every ``ValueError`` raised names the argument the *tool* exposes (``every_min``,
``at``) because the tool layer surfaces the message verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

MIN_INTERVAL_MIN = 1
MAX_INTERVAL_MIN = 1440

_MIN_INTERVAL_S = MIN_INTERVAL_MIN * 60.0
_MAX_INTERVAL_S = MAX_INTERVAL_MIN * 60.0

_EVERY_MIN_HELP = (
    f"every_min must be a whole number of minutes between "
    f"{MIN_INTERVAL_MIN} and {MAX_INTERVAL_MIN}."
)
_AT_HELP = "at must be a 24-hour time like '23:00' (00:00 to 23:59)."
_BOTH_KINDS_HELP = "every_min and at cannot both be set."


def _unknown_kind(kind: object) -> str:
    return f"unknown schedule kind {kind!r}."

# Both grammars are stricter than the stdlib parsers they replace: float() would
# take "1e3" and "1_0" as 1000 and 10, and %M matches one digit, so "9:3" would
# arm 09:03 for a user who meant 09:30.
_EVERY_MIN_RE = re.compile(r"[+-]?[0-9]+")
_AT_RE = re.compile(r"[0-9]{1,2}:[0-9]{2}")

ScheduleKind = Literal["interval", "daily"]


@dataclass(frozen=True)
class Schedule:
    """When a reminder fires: every ``interval_s``, or daily at a local time."""

    kind: ScheduleKind
    interval_s: float | None = None
    at_hour: int | None = None
    at_minute: int | None = None

    def __post_init__(self) -> None:
        if self.kind == "interval":
            if self.interval_s is None:
                raise ValueError(f"every_min is required. {_EVERY_MIN_HELP}")
            if self.at_hour is not None or self.at_minute is not None:
                raise ValueError(_BOTH_KINDS_HELP)
            if not _MIN_INTERVAL_S <= self.interval_s <= _MAX_INTERVAL_S:
                raise ValueError(_EVERY_MIN_HELP)
        elif self.kind == "daily":
            if self.at_hour is None or self.at_minute is None:
                raise ValueError(f"at is required. {_AT_HELP}")
            if self.interval_s is not None:
                raise ValueError(_BOTH_KINDS_HELP)
            if not 0 <= self.at_hour <= 23 or not 0 <= self.at_minute <= 59:
                raise ValueError(_AT_HELP)
        else:
            raise ValueError(_unknown_kind(self.kind))

    def next_due_at(self, after: float) -> float:
        """Wall-clock epoch seconds of the next fire strictly after ``after``.

        Pass the CURRENT time when re-arming a reminder that has just fired,
        never the deadline it fired on. This rolls forward one occurrence only,
        so chaining it from a stale deadline walks forward one step per call and
        leaves the result still in the past — a reminder missed over a weekend
        would then fire once per tick until it caught up.

        Daily schedules are recomputed from the local calendar rather than by
        adding 86400, so a DST transition shifts the real interval to 23 h or
        25 h instead of drifting the local time of day by an hour.
        """
        if self.kind == "interval":
            assert self.interval_s is not None
            return after + self.interval_s

        assert self.at_hour is not None and self.at_minute is not None
        today = datetime.fromtimestamp(after).replace(
            hour=self.at_hour, minute=self.at_minute, second=0, microsecond=0
        )
        due = today.timestamp()
        if due <= after:
            due = (today + timedelta(days=1)).timestamp()
        return due

    def to_row(self) -> dict[str, Any]:
        """The four schedule columns; the caller assembles the reminder row."""
        return {
            "kind": self.kind,
            "interval_s": self.interval_s,
            "at_hour": self.at_hour,
            "at_minute": self.at_minute,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Schedule:
        """Rebuild from the four schedule columns, ignoring any other keys."""
        kind = row.get("kind")
        # Narrows Any|None to the Literal; from_row is the untrusted boundary.
        if kind not in ("interval", "daily"):
            raise ValueError(_unknown_kind(kind))
        interval_s = row.get("interval_s")
        at_hour = row.get("at_hour")
        at_minute = row.get("at_minute")
        return cls(
            kind=kind,
            interval_s=None if interval_s is None else float(interval_s),
            at_hour=None if at_hour is None else int(at_hour),
            at_minute=None if at_minute is None else int(at_minute),
        )

    @classmethod
    def interval_from_minutes(cls, minutes: object) -> Schedule:
        """Parse the tool's ``every_min`` argument into an interval schedule."""
        if isinstance(minutes, bool):
            raise ValueError(_EVERY_MIN_HELP)
        if isinstance(minutes, int):
            value = minutes
        elif isinstance(minutes, float) and minutes.is_integer():
            value = int(minutes)  # a JSON tool argument decodes 30 as 30.0
        elif isinstance(minutes, str) and _EVERY_MIN_RE.fullmatch(minutes.strip()):
            value = int(minutes.strip())
        else:
            raise ValueError(_EVERY_MIN_HELP)
        # Bound before multiplying: int is arbitrary-precision but float is not,
        # so a 400-digit count would raise OverflowError past the ValueError contract.
        if not MIN_INTERVAL_MIN <= value <= MAX_INTERVAL_MIN:
            raise ValueError(_EVERY_MIN_HELP)
        return cls(kind="interval", interval_s=value * 60.0)

    @classmethod
    def daily_from_hhmm(cls, raw: object) -> Schedule:
        """Parse the tool's ``at`` argument (HH:MM, 24-hour) into a daily schedule."""
        if not isinstance(raw, str) or not _AT_RE.fullmatch(raw.strip()):
            raise ValueError(_AT_HELP)
        try:
            parsed = datetime.strptime(raw.strip(), "%H:%M")
        except ValueError:
            raise ValueError(_AT_HELP) from None
        return cls(kind="daily", at_hour=parsed.hour, at_minute=parsed.minute)

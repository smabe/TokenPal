"""Moon phase + illumination for a given date (defaults to today).

Pure math via ``astral.moon``. Phase value is 0-27.99 where:
    0 = new, 7 = first quarter, 14 = full, 21 = last quarter.
Illumination approximates the visible fraction; derived from the phase angle
so it agrees with the named bucket.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action

# (inclusive_low, exclusive_high, name). Widths tuned so the four principal
# phases (new/first-quarter/full/last-quarter) occupy a +/- 0.75 day window and
# the four crescent/gibbous phases fill the gaps.
_PHASE_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 0.75, "new"),
    (0.75, 6.25, "waxing crescent"),
    (6.25, 7.75, "first quarter"),
    (7.75, 13.25, "waxing gibbous"),
    (13.25, 14.75, "full"),
    (14.75, 20.25, "waning gibbous"),
    (20.25, 21.75, "last quarter"),
    (21.75, 27.25, "waning crescent"),
    (27.25, 28.0, "new"),
)


def _phase_name(phase_value: float) -> str:
    for low, high, name in _PHASE_BUCKETS:
        if low <= phase_value < high:
            return name
    return "new"


def _illumination_pct(phase_value: float) -> int:
    """Approximate illuminated fraction from astral's 0-28 phase scale.

    Illumination follows ``(1 - cos(angle)) / 2`` where angle runs 0 to 2pi
    across a full synodic cycle.
    """
    angle = (phase_value / 28.0) * 2 * math.pi
    fraction = (1 - math.cos(angle)) / 2
    return int(round(fraction * 100))


@register_action
class MoonPhaseAction(AbstractAction):
    action_name = "moon_phase"
    description = (
        "Report the moon phase name and approximate illumination percent "
        "for a date (YYYY-MM-DD, defaults to today)."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format. Defaults to today.",
            },
        },
        "required": [],
    }
    safe = True
    requires_confirm = False
    platforms = ("windows", "darwin", "linux")

    async def execute(self, **kwargs: Any) -> ActionResult:
        raw_date = kwargs.get("date")
        if raw_date is None or raw_date == "":
            date = datetime.date.today()
        else:
            try:
                date = datetime.date.fromisoformat(str(raw_date))
            except ValueError:
                return ActionResult(
                    output=f"date must be YYYY-MM-DD, got {raw_date!r}.",
                    success=False,
                )

        from astral import moon  # lazy

        phase_value = moon.phase(date)
        name = _phase_name(phase_value)
        illum = _illumination_pct(phase_value)
        return ActionResult(
            output=f"{date.isoformat()}: {name} moon ({illum}% illuminated)"
        )

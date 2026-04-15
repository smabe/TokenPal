"""City-to-timezone lookup using a bundled table plus ``zoneinfo``.

Coverage is the ~200 cities in ``_timezone_cities.CITY_TO_TZ``. A miss returns
a friendly "city not in table" error rather than falling back to a network
geocode.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.actions.utilities._timezone_cities import lookup

log = logging.getLogger(__name__)


def _format_offset(offset: datetime.timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


@register_action
class TimezoneAction(AbstractAction):
    action_name = "timezone"
    description = "Look up the current local time for a city (e.g. 'Tokyo', 'Paris')."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name (case-insensitive).",
            },
        },
        "required": ["city"],
    }
    safe = True
    requires_confirm = False
    platforms = ("windows", "darwin", "linux")

    async def execute(self, **kwargs: Any) -> ActionResult:
        city = kwargs.get("city")
        if not isinstance(city, str) or not city.strip():
            return ActionResult(output="city is required.", success=False)

        zone_name = lookup(city)
        if zone_name is None:
            log.info("timezone: city '%s' not in table", city)
            return ActionResult(output=f"City '{city}' not in table.", success=False)

        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # lazy

        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            return ActionResult(
                output=f"Zone '{zone_name}' unavailable on this system.",
                success=False,
            )

        now = datetime.datetime.now(tz=zone)
        offset = now.utcoffset() or datetime.timedelta(0)
        stamp = now.strftime("%Y-%m-%d %H:%M")
        return ActionResult(
            output=f"{city.title()}: {stamp} {zone_name} ({_format_offset(offset)})"
        )

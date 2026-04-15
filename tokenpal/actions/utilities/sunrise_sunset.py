"""Today's sunrise / solar noon / sunset for the configured weather location.

Uses ``astral`` for pure-Python astronomy. Coordinates come from
``config.weather.latitude`` / ``longitude`` (already rounded to 1 decimal per
the TokenPal privacy policy) when no explicit lat/lon is passed.
"""

from __future__ import annotations

import datetime
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action


def _load_default_latlon() -> tuple[float, float]:
    """Read lat/lon from config. Lazy so tests/fakes can monkeypatch."""
    from tokenpal.config.loader import load_config

    config = load_config()
    return config.weather.latitude, config.weather.longitude


def _format_time(dt: datetime.datetime) -> str:
    return dt.strftime("%H:%M")


@register_action
class SunriseSunsetAction(AbstractAction):
    action_name = "sunrise_sunset"
    description = (
        "Report today's sunrise, solar noon, and sunset times for the "
        "configured weather location or an explicit lat/lon."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "latitude": {
                "type": "number",
                "description": "Optional latitude override (degrees).",
            },
            "longitude": {
                "type": "number",
                "description": "Optional longitude override (degrees).",
            },
        },
        "required": [],
    }
    safe = True
    requires_confirm = False
    platforms = ("windows", "darwin", "linux")

    async def execute(self, **kwargs: Any) -> ActionResult:
        lat = kwargs.get("latitude")
        lon = kwargs.get("longitude")
        if lat is None or lon is None:
            try:
                lat, lon = _load_default_latlon()
            except Exception as e:  # noqa: BLE001 - defensive against config errors
                return ActionResult(output=f"Cannot load weather config: {e}", success=False)

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            return ActionResult(output="latitude and longitude must be numeric.", success=False)

        if lat_f == 0.0 and lon_f == 0.0:
            return ActionResult(
                output="Weather location not configured. Run /zip first.",
                success=False,
            )

        from astral import LocationInfo  # lazy
        from astral.sun import sun

        try:
            local_tz = datetime.datetime.now().astimezone().tzinfo or datetime.UTC
            loc = LocationInfo("here", "", "UTC", lat_f, lon_f)
            events = sun(loc.observer, date=datetime.date.today(), tzinfo=local_tz)
        except ValueError as e:
            # astral raises ValueError at extreme latitudes (midnight sun / polar night)
            return ActionResult(output=f"No solar events today: {e}", success=False)

        return ActionResult(
            output=(
                f"Sunrise {_format_time(events['sunrise'])}, "
                f"noon {_format_time(events['noon'])}, "
                f"sunset {_format_time(events['sunset'])} "
                f"(lat {lat_f:.1f}, lon {lon_f:.1f})"
            )
        )

"""7-day weather forecast via Open-Meteo, reusing the weather sense's lat/lon."""

from __future__ import annotations

from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.network._location import get_lat_lon, get_temperature_unit
from tokenpal.actions.registry import register_action
from tokenpal.config.weather import unit_symbol

_WMO_CODES: dict[int, str] = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm w/ hail", 99: "thunderstorm w/ heavy hail",
}


def _build_url(lat: float, lon: float, unit: str) -> str:
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode"
        f"&temperature_unit={unit}"
        "&timezone=auto&forecast_days=7"
    )


@register_action
class WeatherForecastWeekAction(AbstractAction):
    action_name = "weather_forecast_week"
    description = "Get a 7-day weather forecast for the configured location."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Ignored. Location comes from config (/zip).",
            },
        },
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        loc = get_lat_lon()
        if loc is None:
            return ActionResult(
                output="No weather location configured. Run /zip first.",
                success=False,
            )
        lat, lon = loc
        temp_unit = get_temperature_unit()
        data, err = await fetch_json(_build_url(lat, lon, temp_unit))
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Forecast fetch failed: {err}", success=False)

        daily = data.get("daily") or {}
        days = daily.get("time") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        codes = daily.get("weathercode") or []
        if not days:
            return ActionResult(output="Forecast had no days.", success=False)

        sym = unit_symbol(temp_unit)
        lines = []
        for i, day in enumerate(days):
            hi = highs[i] if i < len(highs) else "?"
            lo = lows[i] if i < len(lows) else "?"
            code = codes[i] if i < len(codes) else 0
            desc = _WMO_CODES.get(int(code) if isinstance(code, (int, float)) else 0, "unknown")
            lines.append(f"{day}: {desc}, hi {hi}{sym}/lo {lo}{sym}")
        body = "\n".join(lines)
        return ActionResult(output=wrap_result(self.action_name, body))

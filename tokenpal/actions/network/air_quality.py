"""Air quality + pollen via Open-Meteo air-quality endpoint.

Shared URL builder — the two actions (air_quality and pollen_count) reuse
the same response, extracting different fields.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.network._location import get_lat_lon
from tokenpal.actions.registry import register_action

_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    "?latitude={lat}&longitude={lon}"
    "&current=european_aqi,pm10,pm2_5,us_aqi"
    "&hourly=alder_pollen,birch_pollen,grass_pollen,ragweed_pollen"
)


async def _fetch() -> tuple[dict[str, Any] | None, str | None]:
    loc = get_lat_lon()
    if loc is None:
        return None, "no location configured"
    lat, lon = loc
    data, err = await fetch_json(_URL.format(lat=lat, lon=lon))
    if data is None or not isinstance(data, dict):
        return None, err or "empty response"
    return data, None


def _first_hour(values: list[Any] | None) -> Any:
    if not values:
        return None
    return values[0]


@register_action
class AirQualityAction(AbstractAction):
    action_name = "air_quality"
    description = "Get current air quality (AQI, PM2.5, PM10) for the configured location."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "Ignored. Uses config /zip."},
        },
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        data, err = await _fetch()
        if data is None:
            return ActionResult(output=f"Air quality fetch failed: {err}", success=False)
        current = data.get("current") or {}
        aqi = current.get("european_aqi")
        us_aqi = current.get("us_aqi")
        pm25 = current.get("pm2_5")
        pm10 = current.get("pm10")
        body = (
            f"EU AQI: {aqi}, US AQI: {us_aqi}, "
            f"PM2.5: {pm25} ug/m3, PM10: {pm10} ug/m3"
        )
        return ActionResult(output=wrap_result(self.action_name, body))


@register_action
class PollenCountAction(AbstractAction):
    action_name = "pollen_count"
    description = (
        "Get current pollen counts (alder, birch, grass, ragweed) for the configured location."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "Ignored. Uses config /zip."},
        },
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        data, err = await _fetch()
        if data is None:
            return ActionResult(output=f"Pollen fetch failed: {err}", success=False)
        hourly = data.get("hourly") or {}
        alder = _first_hour(hourly.get("alder_pollen"))
        birch = _first_hour(hourly.get("birch_pollen"))
        grass = _first_hour(hourly.get("grass_pollen"))
        ragweed = _first_hour(hourly.get("ragweed_pollen"))
        body = (
            f"alder: {alder}, birch: {birch}, "
            f"grass: {grass}, ragweed: {ragweed} (grains/m3)"
        )
        return ActionResult(output=wrap_result(self.action_name, body))

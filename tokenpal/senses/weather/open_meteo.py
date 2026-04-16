"""Weather sense — current conditions from Open-Meteo (free, no API key)."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from tokenpal.config.weather import unit_symbol
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# WMO weather interpretation codes → short descriptions
_WMO_CODES: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


@register_sense
class OpenMeteoWeather(AbstractSense):
    sense_name = "weather"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 1800.0  # 30 minutes
    reading_ttl_s = 3600.0  # readings valid for 1 hour

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._lat: float | None = None
        self._lon: float | None = None
        self._unit: str = "fahrenheit"
        self._prev_summary: str = ""
        self._consecutive_failures: int = 0
        self._location_label: str = ""

    async def setup(self) -> None:
        lat = self._config.get("latitude", 0.0)
        lon = self._config.get("longitude", 0.0)
        if not lat and not lon:
            log.info("Weather sense: no location configured — use /zip to set it")
            self.disable()
            return
        # Round to 1 decimal for privacy (~11km precision)
        self._lat = round(float(lat), 1)
        self._lon = round(float(lon), 1)
        self._unit = self._config.get("temperature_unit", "fahrenheit")
        self._location_label = self._config.get("location_label", "")
        log.info("Weather sense ready — %.1f, %.1f (%s)", self._lat, self._lon, self._unit)

    async def poll(self) -> SenseReading | None:
        if not self.enabled or self._lat is None:
            return None

        try:
            data = self._fetch()
        except Exception:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                log.warning("Weather sense disabled after 3 consecutive failures")
                self.disable()
            else:
                log.debug("Weather fetch failed (attempt %d)", self._consecutive_failures)
            return None

        self._consecutive_failures = 0
        summary = self._build_summary(data)

        confidence = 1.0 if summary != self._prev_summary else 0.0
        self._prev_summary = summary

        return self._reading(data=data, summary=summary, confidence=confidence)

    def _fetch(self) -> dict[str, Any]:
        """Sync HTTP call to Open-Meteo. Runs via asyncio.to_thread in the brain loop."""
        unit_param = self._unit
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self._lat}&longitude={self._lon}"
            f"&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
            f"&temperature_unit={unit_param}"
            f"&wind_speed_unit=mph"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = json.loads(resp.read())

        current = raw.get("current", {})
        temp = current.get("temperature_2m", 0)
        code = current.get("weather_code", 0)
        wind = current.get("wind_speed_10m", 0)
        humidity = current.get("relative_humidity_2m", 0)
        condition = _WMO_CODES.get(code, "unknown")
        unit_sym = unit_symbol(unit_param)

        return {
            "temperature": temp,
            "unit": unit_sym,
            "weather_code": code,
            "condition": condition,
            "wind_speed_mph": wind,
            "humidity": humidity,
        }

    def _build_summary(self, data: dict[str, Any]) -> str:
        temp = data["temperature"]
        unit = data["unit"]
        condition = data["condition"]
        # Round temperature to int for natural language
        return f"It's {int(temp)}{unit} and {condition} outside"

    async def teardown(self) -> None:
        pass

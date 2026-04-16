"""Weather configuration helpers — geocoding and config.toml writing."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from tokenpal.config.toml_writer import update_config


@dataclass
class GeoLocation:
    lat: float
    lon: float
    label: str


def unit_symbol(unit: str) -> str:
    """Map 'fahrenheit'/'celsius' to 'F'/'C'."""
    return "F" if unit == "fahrenheit" else "C"


def geocode_zip(zipcode: str) -> GeoLocation | None:
    """Geocode a US zip code via Open-Meteo. Returns None on failure."""
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={zipcode}&count=1"
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read())

    results = data.get("results")
    if not results:
        return None

    loc = results[0]
    lat = round(loc.get("latitude", 0), 1)
    lon = round(loc.get("longitude", 0), 1)
    city = loc.get("name", "")
    admin = loc.get("admin1", "")
    label = f"{city}, {admin}" if admin else city
    return GeoLocation(lat=lat, lon=lon, label=label)


def write_weather_config(lat: float, lon: float, label: str) -> None:
    """Write weather location to config.toml, enabling the sense."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("senses", {})["weather"] = True
        data["weather"] = {
            "latitude": lat,
            "longitude": lon,
            "location_label": label,
        }

    update_config(mutate)

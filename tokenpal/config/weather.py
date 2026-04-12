"""Weather configuration helpers — geocoding and config.toml writing."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass


@dataclass
class GeoLocation:
    lat: float
    lon: float
    label: str


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
    from tokenpal.tools.train_voice import _find_config_toml

    config_path = _find_config_toml()
    weather_block = (
        f"[weather]\n"
        f"latitude = {lat}\n"
        f"longitude = {lon}\n"
        f'location_label = "{label}"\n'
    )

    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")

        if re.search(r"^weather\s*=\s*false", content, re.MULTILINE):
            content = re.sub(
                r"^weather\s*=\s*false",
                "weather = true",
                content,
                flags=re.MULTILINE,
            )
        elif "[senses]" in content and "weather" not in content:
            content = content.replace("[senses]", "[senses]\nweather = true", 1)

        if re.search(r"^\[weather\]\s*$", content, re.MULTILINE):
            content = re.sub(
                r"^\[weather\].*?(?=\n\[|\Z)",
                weather_block.rstrip(),
                content,
                flags=re.DOTALL | re.MULTILINE,
            )
        else:
            content = content.rstrip() + "\n\n" + weather_block
    else:
        content = f"[senses]\nweather = true\n\n{weather_block}"

    config_path.write_text(content, encoding="utf-8")

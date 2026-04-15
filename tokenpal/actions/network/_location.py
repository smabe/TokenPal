"""Helper that reads the already-configured weather lat/lon from config.

Phase 2b location-aware tools (weather_forecast_week, pollen, air_quality)
piggyback on the existing weather configuration instead of doing any
fresh geocoding. If the user hasn't set a location, tools error out with
a pointer to /zip.
"""

from __future__ import annotations

from tokenpal.config.loader import load_config


def get_lat_lon() -> tuple[float, float] | None:
    cfg = load_config()
    lat = float(cfg.weather.latitude or 0.0)
    lon = float(cfg.weather.longitude or 0.0)
    if lat == 0.0 and lon == 0.0:
        return None
    # Already rounded to 1 decimal in schema use, but enforce here too.
    return round(lat, 1), round(lon, 1)

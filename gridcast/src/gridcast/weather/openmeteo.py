"""Open-Meteo client (free, no API key) for real hourly weather.

Two endpoints are used:
- the forecast API (recent past + next days) for "live" truth weather,
- the archive API (ERA5 reanalysis) for multi-year training data.
"""

import logging
from datetime import UTC, date, datetime

import httpx

from gridcast.weather.model import WeatherPoint

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = (
    "temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,"
    "shortwave_radiation,precipitation"
)


def _parse(payload: dict) -> dict[datetime, WeatherPoint]:
    hourly = payload["hourly"]
    series: dict[datetime, WeatherPoint] = {}
    for i, stamp in enumerate(hourly["time"]):
        values = (
            hourly["temperature_2m"][i],
            hourly["relative_humidity_2m"][i],
            hourly["cloud_cover"][i],
            hourly["wind_speed_10m"][i],
            hourly["shortwave_radiation"][i],
            hourly["precipitation"][i],
        )
        if any(v is None for v in values):
            continue
        ts = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        series[ts] = WeatherPoint(*(float(v) for v in values))
    return series


def fetch_recent(
    lat: float, lon: float, *, past_days: int = 14, forecast_days: int = 3, timeout: float = 20
) -> dict[datetime, WeatherPoint]:
    params: dict[str, str | int | float] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": HOURLY,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    response = httpx.get(FORECAST_URL, params=params, timeout=timeout)
    response.raise_for_status()
    return _parse(response.json())


def fetch_archive(
    lat: float, lon: float, start: date, end: date, *, timeout: float = 60
) -> dict[datetime, WeatherPoint]:
    params: dict[str, str | int | float] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": HOURLY,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    response = httpx.get(ARCHIVE_URL, params=params, timeout=timeout)
    response.raise_for_status()
    return _parse(response.json())

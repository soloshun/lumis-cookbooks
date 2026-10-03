"""Ground-truth weather: what the sky is actually doing at a station.

Vendors and the demand simulator read this. It prefers real Open-Meteo data (interpolated from
hourly to any timestamp) and falls back to the synthetic generator when offline or when the
requested time is outside the fetched window. The selected backend is reported so a reading's
provenance is always known.
"""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from gridcast.weather import openmeteo
from gridcast.weather.model import WeatherPoint, hash_unit, synthetic_weather

log = logging.getLogger(__name__)


class TruthWeather:
    def __init__(
        self,
        *,
        mode: str = "hybrid",
        refresh_seconds: int = 1800,
        past_days: int = 30,
        forecast_days: int = 4,
    ) -> None:
        if mode not in {"hybrid", "synthetic", "openmeteo"}:
            raise ValueError(f"unknown weather mode {mode!r}")
        self.mode = mode
        self.refresh_seconds = refresh_seconds
        self.past_days = past_days
        self.forecast_days = forecast_days
        self._cache: dict[tuple[float, float], tuple[float, dict[datetime, WeatherPoint]]] = {}
        self._lock = threading.Lock()

    def _series(self, lat: float, lon: float) -> dict[datetime, WeatherPoint]:
        key = (round(lat, 3), round(lon, 3))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.refresh_seconds:
                return cached[1]
        try:
            series = openmeteo.fetch_recent(
                lat, lon, past_days=self.past_days, forecast_days=self.forecast_days
            )
            log.info("open-meteo refreshed", extra={"lat": lat, "lon": lon, "hours": len(series)})
        except Exception as exc:  # network errors must never take the simulator down
            if self.mode == "openmeteo":
                raise
            log.warning("open-meteo unavailable, using synthetic weather", extra={"error": str(exc)})
            series = cached[1] if cached else {}
        with self._lock:
            # Back off for a few minutes after a failure instead of hammering the API.
            stamp = now if series else now - self.refresh_seconds + 300
            self._cache[key] = (stamp, series)
        return series

    def at(self, lat: float, lon: float, ts: datetime) -> tuple[WeatherPoint, str]:
        """Weather at an arbitrary instant and the backend that produced it."""
        ts = ts.astimezone(UTC)
        if self.mode != "synthetic":
            series = self._series(lat, lon)
            lower = ts.replace(minute=0, second=0, microsecond=0)
            upper = lower + timedelta(hours=1)
            if lower in series and upper in series:
                w = (ts - lower).total_seconds() / 3600
                return WeatherPoint.lerp(series[lower], series[upper], w), "open-meteo"
            if lower in series:
                return series[lower], "open-meteo"
        return synthetic_weather(lat, lon, ts), "synthetic"


def vendor_view(point: WeatherPoint, vendor: str, station: str, ts: datetime) -> WeatherPoint:
    """A vendor's measurement of the truth: small, deterministic, vendor-specific error."""
    def jitter(name: str, scale: float) -> float:
        return scale * hash_unit(vendor, station, name, int(ts.timestamp() // 60))

    return WeatherPoint(
        temperature_c=point.temperature_c + jitter("t", 0.25),
        relative_humidity_pct=min(100.0, max(0.0, point.relative_humidity_pct + jitter("rh", 1.5))),
        cloud_cover_pct=min(100.0, max(0.0, point.cloud_cover_pct + jitter("cc", 3.0))),
        wind_speed_ms=max(0.0, point.wind_speed_ms + jitter("ws", 0.3)),
        shortwave_radiation_wm2=max(0.0, point.shortwave_radiation_wm2 * (1 + jitter("sw", 0.03))),
        precipitation_mm=max(0.0, point.precipitation_mm),
    )

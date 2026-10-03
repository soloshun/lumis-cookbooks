"""Synthetic electricity demand driven by weather and the calendar.

Load is built from interpretable components so forecast errors can be reasoned about:

    load = base * daily_shape(hour, day type) * (1 + growth)
         + cooling_sensitivity * max(0, apparent_temp - comfort)
         - rooftop_solar * irradiance / 1000
         + smooth stochastic deviation + per-minute meter noise

All randomness is a deterministic hash of (zone, time), so the same reading can be regenerated
by any process at any time (history backfills, restarts) and tests are reproducible.
"""

import math
from datetime import UTC, datetime

from gridcast.catalog import Zone, catalog
from gridcast.weather.model import WeatherPoint, hash_unit

# Normalized weekday profile for a West-African urban grid: night trough, morning ramp, a
# commercial plateau and an evening residential peak around 19:00-20:00.
_WEEKDAY = [
    0.74, 0.71, 0.69, 0.68, 0.69, 0.73, 0.79, 0.85, 0.90, 0.92, 0.93, 0.94,
    0.94, 0.93, 0.93, 0.93, 0.94, 0.96, 0.99, 1.00, 0.99, 0.94, 0.86, 0.79,
]
_WEEKEND_SCALE = {5: 0.93, 6: 0.88}
_HOLIDAY_SCALE = 0.86


def _shape(ts: datetime) -> float:
    h = ts.hour + ts.minute / 60
    lo = int(h) % 24
    hi = (lo + 1) % 24
    w = h - int(h)
    return _WEEKDAY[lo] * (1 - w) + _WEEKDAY[hi] * w


def apparent_temperature(weather: WeatherPoint) -> float:
    """Simple humidity-adjusted temperature used for cooling demand."""
    return weather.temperature_c + 0.045 * max(0.0, weather.relative_humidity_pct - 55)


def demand_mw(zone: Zone, ts: datetime, weather: WeatherPoint) -> float:
    ts = ts.astimezone(UTC)
    cat = catalog()
    day_scale = _HOLIDAY_SCALE if cat.is_holiday(ts) else _WEEKEND_SCALE.get(ts.weekday(), 1.0)
    years = (ts - datetime(2024, 1, 1, tzinfo=UTC)).days / 365.25
    growth = 1 + 0.045 * years  # ~4.5%/yr demand growth
    base = zone.base_load_mw * _shape(ts) * day_scale * growth
    cooling = zone.cooling_mw_per_degc * max(0.0, apparent_temperature(weather) - zone.comfort_temp_c)
    solar = zone.rooftop_solar_mw * weather.shortwave_radiation_wm2 / 1000

    t_hours = ts.timestamp() / 3600
    drift = 0.0
    for i, period in enumerate((7.0, 31.0, 113.0)):
        phase = math.pi * (1 + hash_unit(zone.id, "drift", i))
        drift += math.sin(2 * math.pi * t_hours / period + phase) / (i + 1)
    drift *= 0.018 * zone.base_load_mw / 1.83
    meter = 0.004 * zone.base_load_mw * hash_unit(zone.id, "meter", int(ts.timestamp() // 60))
    return max(0.0, base + cooling - solar + drift + meter)

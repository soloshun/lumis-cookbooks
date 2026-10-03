"""Weather values and a deterministic synthetic weather generator.

The synthetic generator is the offline fallback for Open-Meteo and the basis for reproducible
tests. It is deterministic in (latitude, longitude, timestamp), so every process that asks for
the same place and time gets the same answer without sharing state.
"""

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

VARIABLES = (
    "temperature_c",
    "relative_humidity_pct",
    "cloud_cover_pct",
    "wind_speed_ms",
    "shortwave_radiation_wm2",
    "precipitation_mm",
)


@dataclass(frozen=True)
class WeatherPoint:
    temperature_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    wind_speed_ms: float
    shortwave_radiation_wm2: float
    precipitation_mm: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 3) for k, v in asdict(self).items()}

    @staticmethod
    def lerp(a: "WeatherPoint", b: "WeatherPoint", w: float) -> "WeatherPoint":
        return WeatherPoint(*(getattr(a, k) * (1 - w) + getattr(b, k) * w for k in VARIABLES))


def hash_unit(*keys: object) -> float:
    """Deterministic pseudo-random number in [-1, 1] for the given keys."""
    digest = hashlib.blake2b("|".join(map(str, keys)).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**63 - 1.0


def solar_elevation_factor(lat: float, lon: float, ts: datetime) -> float:
    """Approximate sin(solar elevation), clipped at 0 (night)."""
    ts = ts.astimezone(UTC)
    doy = ts.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
    solar_hour = ts.hour + ts.minute / 60 + lon / 15
    hour_angle = math.radians(15 * (solar_hour - 12))
    lat_r = math.radians(lat)
    sin_elev = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(
        hour_angle
    )
    return max(0.0, sin_elev)


def synthetic_weather(lat: float, lon: float, ts: datetime) -> WeatherPoint:
    """Plausible West-African weather: harmattan/rainy seasonality, diurnal cycle, smooth noise."""
    ts = ts.astimezone(UTC)
    t_days = ts.timestamp() / 86400.0
    doy = ts.timetuple().tm_yday
    hour = ts.hour + ts.minute / 60 + lon / 15
    site = f"{lat:.2f},{lon:.2f}"

    def smooth(name: str, scale: float) -> float:
        total = 0.0
        for i, period in enumerate((2.3, 5.1, 11.7)):
            phase = math.pi * (1 + hash_unit(site, name, i))
            total += math.sin(2 * math.pi * t_days / period + phase) / (i + 1)
        return scale * total / 1.83

    continentality = min(1.0, max(0.0, (lat - 4.5) / 5.5))
    base = 26.4 + 1.2 * continentality
    seasonal = 1.6 * math.cos(2 * math.pi * (doy - 70) / 365)
    harmattan = max(0.0, math.cos(2 * math.pi * (doy - 15) / 365))
    amplitude = 3.2 + 3.5 * continentality + 2.0 * harmattan * continentality
    diurnal = amplitude * math.sin(2 * math.pi * (hour - 9) / 24)
    temperature = base + seasonal + diurnal + smooth("temp", 1.4)

    rainy = max(0.0, math.cos(2 * math.pi * (doy - 170) / 365))
    cloud = 35 + 35 * rainy + smooth("cloud", 22) + 8 * math.sin(2 * math.pi * (hour - 14) / 24)
    cloud = min(100.0, max(0.0, cloud))
    humidity = 84 - 2.4 * (temperature - 24) - 25 * harmattan * continentality + smooth("rh", 5)
    humidity = min(100.0, max(12.0, humidity))
    wind = max(0.2, 3.1 + 1.2 * math.sin(2 * math.pi * (hour - 15) / 24) + smooth("wind", 1.0))
    radiation = 1050 * solar_elevation_factor(lat, lon, ts) * (1 - 0.72 * cloud / 100)
    precip = max(0.0, (cloud - 78) / 10 * (0.5 + rainy) + smooth("precip", 0.4))
    return WeatherPoint(temperature, humidity, cloud, wind, radiation, precip)

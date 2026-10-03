"""Feature definitions shared by training and serving.

Forecasts are made at an hour-aligned information cut-off `A` (as-of) for target hours
`T = A + k hours`, k = 1..horizon. Only data strictly before `A` may be used.

    load_lag_24h    mean load of hour T-24h, or T-48h when T-24h is not yet observed (k = 24)
    load_lag_168h   mean load of hour T-168h (same hour last week)
    load_mean_24h   mean of hourly means over [A-24h, A)
    load_recent_3h  mean of hourly means over [A-3h, A)
    weather         vendor forecast for T issued at or before A
    calendar        hour, day of week, holiday flag of T

Load features are normalized by the zone's base load before reaching the model, so one model
serves every zone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from gridcast.catalog import catalog
from gridcast.weather.model import VARIABLES

HOUR = timedelta(hours=1)
LOAD_FEATURES = ("load_lag_24h", "load_lag_168h", "load_mean_24h", "load_recent_3h")
CALENDAR_FEATURES = ("hour", "dow", "is_holiday")
MODEL_FEATURES = (
    *VARIABLES, *CALENDAR_FEATURES, "horizon_h",
    "load_lag_24h_n", "load_lag_168h_n", "load_mean_24h_n", "load_recent_3h_n", "zone_idx",
)
CATEGORICAL = ("zone_idx",)
LOOKBACK = timedelta(hours=170)  # 168 h lag plus the k=24 fallback margin


@dataclass(frozen=True)
class FeatureRow:
    zone_id: str
    target_ts: datetime
    horizon_h: int
    weather: Mapping[str, float]
    hour: int
    dow: int
    is_holiday: bool
    load_lag_24h: float
    load_lag_168h: float
    load_mean_24h: float
    load_recent_3h: float

    def record(self) -> dict:
        return {
            "zone_id": self.zone_id, "target_ts": self.target_ts, "horizon_h": self.horizon_h,
            **{k: float(self.weather[k]) for k in VARIABLES},
            "hour": self.hour, "dow": self.dow, "is_holiday": self.is_holiday,
            "load_lag_24h": self.load_lag_24h, "load_lag_168h": self.load_lag_168h,
            "load_mean_24h": self.load_mean_24h, "load_recent_3h": self.load_recent_3h,
        }


class MissingHistory(ValueError):
    """Not enough demand history before as-of to build lag features."""


def floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def lag_hour(as_of: datetime, target: datetime) -> datetime:
    candidate = target - timedelta(hours=24)
    return candidate if candidate < as_of else target - timedelta(hours=48)


def window_mean(hourly: Mapping[datetime, float], as_of: datetime, hours: int) -> float:
    values = [hourly[as_of - HOUR * i] for i in range(1, hours + 1) if as_of - HOUR * i in hourly]
    if not values:
        raise MissingHistory(f"no demand in the {hours} h before {as_of.isoformat()}")
    return float(np.mean(values))


def lookup(hourly: Mapping[datetime, float], hour: datetime) -> float:
    try:
        return hourly[hour]
    except KeyError:
        raise MissingHistory(f"no demand for hour {hour.isoformat()}") from None


def build_row(
    zone_id: str,
    as_of: datetime,
    horizon: int,
    hourly_load: Mapping[datetime, float],
    weather: Mapping[str, float],
    *,
    mean_24h: float | None = None,
    recent_3h: float | None = None,
) -> FeatureRow:
    target = as_of + HOUR * horizon
    return FeatureRow(
        zone_id=zone_id,
        target_ts=target,
        horizon_h=horizon,
        weather=weather,
        hour=target.hour,
        dow=target.weekday(),
        is_holiday=catalog().is_holiday(target),
        load_lag_24h=lookup(hourly_load, lag_hour(as_of, target)),
        load_lag_168h=lookup(hourly_load, target - timedelta(hours=168)),
        load_mean_24h=mean_24h if mean_24h is not None else window_mean(hourly_load, as_of, 24),
        load_recent_3h=recent_3h if recent_3h is not None else window_mean(hourly_load, as_of, 3),
    )


def zone_index() -> dict[str, int]:
    return {z.id: i for i, z in enumerate(catalog().zones)}


def to_matrix(records: Sequence[Mapping]) -> np.ndarray:
    """Model design matrix (rows follow `MODEL_FEATURES`) from feature records."""
    cat = catalog()
    zidx = zone_index()
    out = np.empty((len(records), len(MODEL_FEATURES)), dtype=np.float64)
    for i, r in enumerate(records):
        base = cat.zone(r["zone_id"]).base_load_mw
        out[i] = [
            *(r[v] for v in VARIABLES), r["hour"], r["dow"], float(r["is_holiday"]),
            r["horizon_h"],
            r["load_lag_24h"] / base, r["load_lag_168h"] / base,
            r["load_mean_24h"] / base, r["load_recent_3h"] / base,
            zidx[r["zone_id"]],
        ]
    return out

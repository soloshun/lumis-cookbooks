"""Serving-time feature builders over `raw.*`.

Two builders produce identical features with very different database access patterns:

`hourly`  (feature-service <= 1.6) aggregates demand to hourly buckets inside PostgreSQL with
          one grouped scan, plus one weather query. ~3 queries per run.

`minute`  (feature-service 1.7, "native-resolution lags") fetches raw one-minute readings for
          every lag hour of every target separately (bucketed with date_trunc) and averages them
          in Python, recomputing the trailing windows per target. Same numbers, ~2,500 queries
          per run, each scanning the zone's full history.

Which builder runs is a release flag (`lag_resolution`) baked into the feature-service image.
"""

import contextvars
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, event, text

from gridcast.catalog import catalog
from gridcast.features.engineering import HOUR, LOOKBACK, FeatureRow, build_row, lag_hour
from gridcast.weather.model import VARIABLES

_query_counter: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "feature_queries", default=None
)


def install_query_counter(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        counter = _query_counter.get()
        if counter is not None:
            counter[0] += 1


class QueryCount:
    """Count SQL statements executed in this context (thread-safe via contextvars)."""

    def __enter__(self) -> "QueryCount":
        self.value = [0]
        self._token = _query_counter.set(self.value)
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        _query_counter.reset(self._token)

    @property
    def count(self) -> int:
        return self.value[0]


WEATHER_SQL = text(f"""
    SELECT DISTINCT ON (station_id, valid_at)
           station_id, valid_at, {", ".join(VARIABLES)}
    FROM raw.weather_forecasts
    WHERE issued_at <= :as_of AND valid_at > :as_of AND valid_at <= :until
    ORDER BY station_id, valid_at, issued_at DESC
""")

LATEST_OBS_SQL = text(f"""
    SELECT DISTINCT ON (station_id) station_id, {", ".join(VARIABLES)}
    FROM raw.weather_observations
    WHERE observed_at < :as_of
    ORDER BY station_id, observed_at DESC
""")


def _weather(conn: Connection, as_of: datetime, horizon: int):
    forecasts: dict[tuple[str, datetime], dict[str, float]] = {}
    rows = conn.execute(WEATHER_SQL, {"as_of": as_of, "until": as_of + HOUR * horizon})
    for row in rows.mappings():
        forecasts[(row["station_id"], row["valid_at"])] = {v: row[v] for v in VARIABLES}
    persistence = {
        row["station_id"]: {v: row[v] for v in VARIABLES}
        for row in conn.execute(LATEST_OBS_SQL, {"as_of": as_of}).mappings()
    }
    return forecasts, persistence


def _weather_for(forecasts, persistence, station_id: str, target: datetime, stats: dict):
    value = forecasts.get((station_id, target))
    if value is None:
        stats["weather_fallbacks"] += 1
        value = persistence.get(station_id)
        if value is None:
            raise ValueError(f"no weather forecast or observation for {station_id}")
    return value


def build_hourly(conn: Connection, as_of: datetime, horizon: int) -> tuple[list[FeatureRow], dict]:
    stats = {"weather_fallbacks": 0}
    hourly: dict[str, dict[datetime, float]] = defaultdict(dict)
    rows = conn.execute(text("""
        SELECT zone_id, date_trunc('hour', ts) AS hour, avg(load_mw) AS load_mw
        FROM raw.demand_readings
        WHERE ts >= :start AND ts < :as_of
        GROUP BY zone_id, date_trunc('hour', ts)
    """), {"start": as_of - LOOKBACK, "as_of": as_of})
    for zone_id, hour, load in rows:
        hourly[zone_id][hour] = float(load)
    forecasts, persistence = _weather(conn, as_of, horizon)
    out = []
    for zone in catalog().zones:
        for k in range(1, horizon + 1):
            target = as_of + HOUR * k
            weather = _weather_for(forecasts, persistence, zone.station_id, target, stats)
            out.append(build_row(zone.id, as_of, k, hourly[zone.id], weather))
    return out, stats


# Buckets are matched on date_trunc(ts): readable, but not sargable on the (zone_id, ts) key,
# so every lookup walks the zone's whole history. Cost grows with retained data.
MINUTE_SQL = text("""
    SELECT load_mw FROM raw.demand_readings
    WHERE zone_id = :zone_id AND date_trunc('hour', ts) = :start AND ts < :as_of
""")


def _hour_mean(conn: Connection, zone_id: str, hour: datetime, as_of: datetime) -> float | None:
    values = conn.execute(
        MINUTE_SQL, {"zone_id": zone_id, "start": hour, "as_of": as_of}
    ).scalars().all()
    return sum(values) / len(values) if values else None


def build_minute(conn: Connection, as_of: datetime, horizon: int) -> tuple[list[FeatureRow], dict]:
    stats = {"weather_fallbacks": 0}
    forecasts, persistence = _weather(conn, as_of, horizon)
    out = []
    for zone in catalog().zones:
        for k in range(1, horizon + 1):
            target = as_of + HOUR * k
            needed = [lag_hour(as_of, target), target - timedelta(hours=168)]
            needed += [as_of - HOUR * i for i in range(1, 25)]
            hourly = {}
            for hour in needed:
                mean = _hour_mean(conn, zone.id, hour, as_of)
                if mean is not None:
                    hourly[hour] = mean
            weather = _weather_for(forecasts, persistence, zone.station_id, target, stats)
            out.append(build_row(zone.id, as_of, k, hourly, weather))
    return out, stats


BUILDERS = {"hourly": build_hourly, "minute": build_minute}

"""Weather vendor simulator (namespace `vendors`).

Simulates a commercial weather-data provider: 5-minute station observations and hourly
forecasts, derived from real Open-Meteo weather plus vendor-specific measurement error and
lead-time-dependent forecast error. Two instances run (primary and fallback vendor) with
different vendor IDs, so their data differ slightly, like real competing providers.

Supported vendor faults: `stale` (keeps answering 200 OK with fresh timestamps but frozen
values), `outage` (503), `slow` (added latency).
"""

import math
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from gridcast.catalog import catalog
from gridcast.services.common import create_app, serve
from gridcast.services.vendor_faults import Faults, admin_router
from gridcast.weather.model import WeatherPoint, hash_unit
from gridcast.weather.truth import TruthWeather, vendor_view

OBS_STEP = timedelta(minutes=5)
MAX_RANGE = timedelta(days=15)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEATHER_VENDOR_", env_file=".env", extra="ignore")

    vendor_id: str = "wx-primary"
    truth_mode: str = "hybrid"
    admin_token: str = ""


class Observation(BaseModel):
    station_id: str
    observed_at: datetime
    temperature_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    wind_speed_ms: float
    shortwave_radiation_wm2: float
    precipitation_mm: float


class ForecastHour(BaseModel):
    valid_at: datetime
    temperature_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    wind_speed_ms: float
    shortwave_radiation_wm2: float
    precipitation_mm: float


class Forecast(BaseModel):
    station_id: str
    provider: str
    issued_at: datetime
    hours: list[ForecastHour]


def _floor(ts: datetime, step: timedelta) -> datetime:
    seconds = step.total_seconds()
    return datetime.fromtimestamp(ts.timestamp() // seconds * seconds, UTC)


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    truth = TruthWeather(mode=settings.truth_mode)
    faults = Faults({"stale", "outage", "slow"})
    app = create_app(
        f"weather-vendor-{settings.vendor_id}",
        description="Simulated commercial weather vendor (external dependency).",
    )
    app.include_router(admin_router(faults, settings.admin_token))
    cat = catalog()
    vendor = settings.vendor_id

    def station(station_id: str):
        try:
            return cat.station(station_id)
        except StopIteration:
            raise HTTPException(404, f"unknown station {station_id}") from None

    def observe(station_id: str, ts: datetime) -> WeatherPoint:
        st = station(station_id)
        fault = faults.state
        if fault.mode == "stale" and fault.since and ts > fault.since:
            ts = _floor(fault.since, OBS_STEP)  # frozen snapshot, relabelled with fresh time
        point, _ = truth.at(st.latitude, st.longitude, ts)
        return vendor_view(point, vendor, station_id, ts)

    def forecast_value(station_id: str, issued: datetime, valid: datetime) -> WeatherPoint:
        st = station(station_id)
        point, _ = truth.at(st.latitude, st.longitude, valid)
        lead_h = max(0.0, (valid - issued).total_seconds() / 3600)
        growth = math.sqrt(lead_h / 24)

        def err(name: str, scale: float) -> float:
            # Error correlated within an issue cycle, so forecasts drift rather than flicker.
            return scale * growth * hash_unit(vendor, station_id, name, issued.isoformat(),
                                              int(lead_h // 6))

        return WeatherPoint(
            temperature_c=point.temperature_c + err("t", 1.6),
            relative_humidity_pct=min(100.0, max(0.0, point.relative_humidity_pct + err("rh", 7))),
            cloud_cover_pct=min(100.0, max(0.0, point.cloud_cover_pct + err("cc", 18))),
            wind_speed_ms=max(0.0, point.wind_speed_ms + err("ws", 1.2)),
            shortwave_radiation_wm2=max(0.0, point.shortwave_radiation_wm2 * (1 + err("sw", 0.25))),
            precipitation_mm=max(0.0, point.precipitation_mm + err("p", 0.4)),
        )

    @app.middleware("http")
    async def transport_faults(request, call_next):  # noqa: ANN001
        if request.url.path.startswith("/v1/"):
            response = await faults.apply_transport_faults()
            if response is not None:
                return response
        return await call_next(request)

    @app.get("/v1/stations", tags=["data"])
    def stations() -> list[dict]:
        return [s.model_dump() for s in cat.stations]

    @app.get("/v1/observations", tags=["data"], response_model=list[Observation])
    def observations(
        station_id: str, start: datetime, end: datetime | None = None
    ) -> list[Observation]:
        now = datetime.now(UTC)
        end = min(end or now, now)
        if start.tzinfo is None or end - start > MAX_RANGE:
            raise HTTPException(422, "start must be timezone-aware and range <= 15 days")
        out = []
        ts = _floor(start, OBS_STEP)
        if ts < start:
            ts += OBS_STEP
        while ts <= end:
            out.append(Observation(station_id=station_id, observed_at=ts,
                                   **observe(station_id, ts).as_dict()))
            ts += OBS_STEP
        return out

    @app.get("/v1/observations/latest", tags=["data"], response_model=Observation)
    def latest(station_id: str) -> Observation:
        ts = _floor(datetime.now(UTC), OBS_STEP)
        return Observation(station_id=station_id, observed_at=ts, **observe(station_id, ts).as_dict())

    @app.get("/v1/forecast", tags=["data"], response_model=Forecast)
    def forecast(station_id: str, hours: int = Query(default=48, ge=1, le=96)) -> Forecast:
        station(station_id)
        now = datetime.now(UTC)
        issued = _floor(now, timedelta(hours=1))
        fault = faults.state
        lag = timedelta(0)
        source_issue = issued
        if fault.mode == "stale" and fault.since:
            # The vendor keeps re-serving its last snapshot, relabelled as a fresh issue.
            source_issue = _floor(fault.since, timedelta(hours=1))
            lag = issued - source_issue
        result = []
        for h in range(1, hours + 1):
            valid = issued + timedelta(hours=h)
            value = forecast_value(station_id, source_issue, valid - lag)
            result.append(ForecastHour(valid_at=valid, **value.as_dict()))
        return Forecast(station_id=station_id, provider=vendor, issued_at=issued, hours=result)

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

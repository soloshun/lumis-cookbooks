"""Ingestion service: pulls vendor data into `raw.*`.

Three independent loops (weather observations, weather forecasts, demand) each fetch everything
newer than what is already stored, validate the vendor payload against the contract GridCast
expects, upsert it and record an `raw.ingestion_batches` audit row. A failed cycle is logged,
counted and retried on the next tick; one dataset failing never blocks the others.

Which weather vendor is used is configuration (`INGEST_WEATHER_PROVIDER`), so switching to
the fallback vendor is a normal, reviewable config change.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from opentelemetry import metrics, trace
from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gridcast.catalog import catalog
from gridcast.db.engine import make_engine
from gridcast.db.schema import (
    demand_readings,
    ingestion_batches,
    weather_forecasts,
    weather_observations,
)
from gridcast.services.common import create_app, serve
from gridcast.telemetry import SECONDS_BUCKETS
from gridcast.weather.model import VARIABLES

log = logging.getLogger(__name__)
tracer = trace.get_tracer("gridcast.ingestion")
meter = metrics.get_meter("gridcast.ingestion")
BATCHES = meter.create_counter("gridcast.ingest.batches", description="Ingestion batches")
ROWS = meter.create_counter("gridcast.ingest.rows", description="Rows upserted")
BATCH_SECONDS = meter.create_histogram(
    "gridcast.ingest.batch.duration", unit="s", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGEST_", env_file=".env", extra="ignore")

    weather_provider: str = "wx-primary"
    weather_primary_url: str = "http://localhost:8084"
    weather_secondary_url: str = "http://localhost:8085"
    grid_telemetry_url: str = "http://localhost:8086"
    observations_interval_seconds: int = 60
    forecasts_interval_seconds: int = 900
    demand_interval_seconds: int = 60
    http_timeout_seconds: float = 15.0
    initial_lookback_hours: int = 6

    def weather_url(self) -> str:
        return {
            "wx-primary": self.weather_primary_url,
            "wx-secondary": self.weather_secondary_url,
        }[self.weather_provider]


# Vendor payload contracts --------------------------------------------------------------------
class ObservationIn(BaseModel):
    station_id: str
    observed_at: datetime
    temperature_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    wind_speed_ms: float
    shortwave_radiation_wm2: float
    precipitation_mm: float


class ForecastHourIn(BaseModel):
    valid_at: datetime
    temperature_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    wind_speed_ms: float
    shortwave_radiation_wm2: float
    precipitation_mm: float


class ForecastIn(BaseModel):
    station_id: str
    provider: str
    issued_at: datetime
    hours: list[ForecastHourIn]


class DemandIn(BaseModel):
    zone_id: str
    ts: datetime
    load_mw: float
    quality: str = "good"


@dataclass
class DatasetStatus:
    last_success: datetime | None = None
    last_error: str | None = None
    last_rows: int = 0
    freshness_seconds: float | None = None
    consecutive_failures: int = 0


@dataclass
class Ingestor:
    settings: Settings
    engine: Engine
    client: httpx.Client
    status: dict[str, DatasetStatus] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------------------------
    def _record(self, dataset: str, source: str, started: datetime, rows: int, error: str | None):
        with self.engine.begin() as conn:
            conn.execute(insert(ingestion_batches).values(
                dataset=dataset, source=source, started_at=started,
                finished_at=datetime.now(UTC), rows=rows,
                status="error" if error else "ok", error=error,
            ))

    def _latest(self, column, *filters) -> datetime | None:
        with self.engine.connect() as conn:
            return conn.execute(select(func.max(column)).where(*filters)).scalar()

    def _run(self, dataset: str, source: str, fn) -> int:
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        status = self.status.setdefault(dataset, DatasetStatus())
        with tracer.start_as_current_span(f"ingest {dataset}", attributes={
            "gridcast.dataset": dataset, "gridcast.source": source,
        }) as span:
            try:
                rows = fn()
            except Exception as exc:  # recorded and retried next cycle
                error = _summarize(exc)
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error))
                status.last_error = error
                status.consecutive_failures += 1
                BATCHES.add(1, {"dataset": dataset, "source": source, "status": "error"})
                log.error("ingestion batch failed", extra={
                    "dataset": dataset, "source": source, "error": error,
                    "consecutive_failures": status.consecutive_failures,
                })
                try:
                    self._record(dataset, source, started, 0, error)
                except Exception:
                    log.exception("could not record failed batch", extra={"dataset": dataset})
                return 0
            finally:
                BATCH_SECONDS.record(time.perf_counter() - t0, {"dataset": dataset})
            span.set_attribute("gridcast.rows", rows)
        self._record(dataset, source, started, rows, None)
        status.last_success, status.last_error, status.last_rows = datetime.now(UTC), None, rows
        status.consecutive_failures = 0
        BATCHES.add(1, {"dataset": dataset, "source": source, "status": "ok"})
        ROWS.add(rows, {"dataset": dataset, "source": source})
        log.info("ingestion batch ok", extra={"dataset": dataset, "source": source, "rows": rows})
        return rows

    def _start_for(self, latest: datetime | None, cap: timedelta) -> datetime:
        now = datetime.now(UTC)
        floor = now - cap
        if latest is None:
            return now - timedelta(hours=self.settings.initial_lookback_hours)
        return max(latest + timedelta(seconds=1), floor)

    # -- datasets --------------------------------------------------------------------------
    def ingest_observations(self, lookback: timedelta = timedelta(days=2)) -> int:
        provider = self.settings.weather_provider

        def work() -> int:
            total = 0
            for station in catalog().stations:
                latest = self._latest(weather_observations.c.observed_at,
                                      weather_observations.c.station_id == station.id)
                start = self._start_for(latest, lookback)
                response = self.client.get(f"{self.settings.weather_url()}/v1/observations",
                                           params={"station_id": station.id,
                                                   "start": start.isoformat()})
                response.raise_for_status()
                items = [ObservationIn.model_validate(o) for o in response.json()]
                if not items:
                    continue
                rows = [{**o.model_dump(), "provider_id": provider} for o in items]
                stmt = pg_insert(weather_observations).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["station_id", "observed_at"],
                    set_={c: stmt.excluded[c] for c in (*VARIABLES, "provider_id")}
                    | {"ingested_at": func.now()},
                )
                with self.engine.begin() as conn:
                    conn.execute(stmt)
                total += len(rows)
            return total

        return self._run("weather_observations", provider, work)

    def ingest_forecasts(self) -> int:
        provider = self.settings.weather_provider

        def work() -> int:
            total = 0
            for station in catalog().stations:
                response = self.client.get(f"{self.settings.weather_url()}/v1/forecast",
                                           params={"station_id": station.id, "hours": 48})
                response.raise_for_status()
                fc = ForecastIn.model_validate(response.json())
                rows = [{"station_id": fc.station_id, "issued_at": fc.issued_at,
                         "provider_id": provider, **h.model_dump()} for h in fc.hours]
                stmt = pg_insert(weather_forecasts).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["station_id", "issued_at", "valid_at"],
                    set_={c: stmt.excluded[c] for c in (*VARIABLES, "provider_id")}
                    | {"ingested_at": func.now()},
                )
                with self.engine.begin() as conn:
                    conn.execute(stmt)
                total += len(rows)
            return total

        return self._run("weather_forecasts", provider, work)

    def ingest_demand(self, lookback: timedelta = timedelta(days=2)) -> int:
        def work() -> int:
            total = 0
            for zone in catalog().zones:
                latest = self._latest(demand_readings.c.ts, demand_readings.c.zone_id == zone.id)
                start = self._start_for(latest, lookback)
                response = self.client.get(f"{self.settings.grid_telemetry_url}/v1/load",
                                           params={"zone_id": zone.id, "start": start.isoformat()})
                response.raise_for_status()
                payload = response.json()
                try:
                    items = [DemandIn.model_validate(r) for r in payload["readings"]]
                except ValidationError as exc:
                    raise ContractViolation(
                        f"grid-telemetry payload no longer matches contract "
                        f"(api_version={payload.get('api_version')}): "
                        f"{exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
                    ) from exc
                if not items:
                    continue
                rows = [i.model_dump() for i in items]
                for chunk in range(0, len(rows), 2000):
                    stmt = pg_insert(demand_readings).values(rows[chunk:chunk + 2000])
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["zone_id", "ts"],
                        set_={"load_mw": stmt.excluded.load_mw, "quality": stmt.excluded.quality,
                              "ingested_at": func.now()},
                    )
                    with self.engine.begin() as conn:
                        conn.execute(stmt)
                total += len(rows)
            return total

        return self._run("demand", "grid-telemetry", work)

    def backfill(self, days: int) -> dict[str, int]:
        """Historical load for a fresh estate: walk the vendor APIs in windows."""
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        counts = {"weather_observations": 0, "demand": 0}
        cursor = start
        while cursor < now:
            end = min(cursor + timedelta(days=1), now)
            for zone in catalog().zones:
                r = self.client.get(f"{self.settings.grid_telemetry_url}/v1/load", params={
                    "zone_id": zone.id, "start": cursor.isoformat(), "end": end.isoformat()})
                r.raise_for_status()
                rows = [DemandIn.model_validate(x).model_dump() for x in r.json()["readings"]]
                for chunk in range(0, len(rows), 2000):
                    stmt = pg_insert(demand_readings).values(rows[chunk:chunk + 2000])
                    with self.engine.begin() as conn:
                        conn.execute(stmt.on_conflict_do_nothing())
                counts["demand"] += len(rows)
            for station in catalog().stations:
                r = self.client.get(f"{self.settings.weather_url()}/v1/observations", params={
                    "station_id": station.id, "start": cursor.isoformat(), "end": end.isoformat()})
                r.raise_for_status()
                rows = [{**ObservationIn.model_validate(o).model_dump(),
                         "provider_id": self.settings.weather_provider} for o in r.json()]
                if rows:
                    with self.engine.begin() as conn:
                        conn.execute(pg_insert(weather_observations).values(rows)
                                     .on_conflict_do_nothing())
                counts["weather_observations"] += len(rows)
            log.info("backfill window done", extra={"window_end": end.isoformat(), **counts})
            cursor = end
        self.ingest_forecasts()
        return counts

    def refresh_freshness(self) -> None:
        queries = {
            "weather_observations": "SELECT extract(epoch FROM now() - max(observed_at)) "
                                    "FROM raw.weather_observations",
            "weather_forecasts": "SELECT extract(epoch FROM now() - max(issued_at)) "
                                 "FROM raw.weather_forecasts",
            "demand": "SELECT extract(epoch FROM now() - max(ts)) FROM raw.demand_readings",
        }
        with self.engine.connect() as conn:
            for dataset, sql in queries.items():
                value = conn.execute(text(sql)).scalar()
                self.status.setdefault(dataset, DatasetStatus()).freshness_seconds = (
                    float(value) if value is not None else None
                )


class ContractViolation(Exception):
    """The vendor payload no longer matches the agreed interface."""


def _summarize(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from {exc.request.url.host}{exc.request.url.path}"
    if isinstance(exc, httpx.TransportError):
        return f"{type(exc).__name__} contacting vendor: {exc}"[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


def build_ingestor(settings: Settings) -> Ingestor:
    engine = make_engine(application_name="ingestion")
    client = httpx.Client(timeout=settings.http_timeout_seconds)
    return Ingestor(settings=settings, engine=engine, client=client)


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    holder: dict[str, Ingestor] = {}

    async def loop(name: str, interval: int, fn) -> None:
        while True:
            await asyncio.to_thread(fn)
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ingestor = build_ingestor(settings)
        holder["ingestor"] = ingestor

        def freshness_cb(options):  # noqa: ANN001
            from opentelemetry.metrics import Observation

            for dataset, st in ingestor.status.items():
                if st.freshness_seconds is not None:
                    yield Observation(st.freshness_seconds, {"dataset": dataset})

        meter.create_observable_gauge("gridcast.data.freshness", callbacks=[freshness_cb],
                                      unit="s", description="Age of newest stored record")

        def refresh() -> None:
            try:
                ingestor.refresh_freshness()
            except Exception as exc:
                log.warning("freshness refresh failed", extra={"error": _summarize(exc)})

        tasks = [
            asyncio.create_task(loop("observations", settings.observations_interval_seconds,
                                     ingestor.ingest_observations)),
            asyncio.create_task(loop("forecasts", settings.forecasts_interval_seconds,
                                     ingestor.ingest_forecasts)),
            asyncio.create_task(loop("demand", settings.demand_interval_seconds,
                                     ingestor.ingest_demand)),
            asyncio.create_task(loop("freshness", 30, refresh)),
        ]
        log.info("ingestion started", extra={"weather_provider": settings.weather_provider})
        yield
        for task in tasks:
            task.cancel()
        ingestor.client.close()
        ingestor.engine.dispose()

    def readiness() -> tuple[bool, str]:
        # Ready once started. Vendor failures are reported via metrics and /status, not by
        # flipping readiness: a healthy service with a broken upstream should not be restarted.
        return ("ingestor" in holder, "ok" if "ingestor" in holder else "starting")

    app = create_app("ingestion", lifespan=lifespan, readiness=readiness,
                     description="Pulls weather and demand from vendors into raw tables.")

    @app.get("/status", tags=["ops"])
    def status() -> dict:
        ingestor = holder.get("ingestor")
        return {
            "weather_provider": settings.weather_provider,
            "datasets": {k: v.__dict__ for k, v in (ingestor.status if ingestor else {}).items()},
        }

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

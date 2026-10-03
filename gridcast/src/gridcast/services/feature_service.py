"""Feature service: builds model inputs for a forecast cycle.

POST /v1/feature-runs builds features for every zone and the next `horizon_hours` hours at an
hour-aligned cut-off and persists them in `features.*`. The lag-feature builder is selected by
the release flag `lag_resolution` (see `gridcast.features.store`).
"""

import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert, select, update

from gridcast.db.engine import make_engine
from gridcast.db.schema import feature_runs, forecast_features
from gridcast.features.engineering import MissingHistory, floor_hour
from gridcast.features.store import BUILDERS, QueryCount, install_query_counter
from gridcast.services.common import create_app, serve
from gridcast.telemetry import SECONDS_BUCKETS, initialize_counters

log = logging.getLogger(__name__)
tracer = trace.get_tracer("gridcast.features")
meter = metrics.get_meter("gridcast.features")
BUILD_SECONDS = meter.create_histogram(
    "gridcast.feature.build.duration", unit="s", description="Feature build wall-clock time",
    explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
DB_QUERIES = meter.create_counter("gridcast.feature.db.queries", description="SQL statements")
BUILDS = meter.create_counter("gridcast.feature.builds", description="Feature builds by status")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FEATURES_", env_file=".env", extra="ignore")

    default_horizon_hours: int = 24


class FeatureRunRequest(BaseModel):
    as_of: datetime | None = Field(default=None, description="Defaults to now; floored to hour")
    horizon_hours: int = Field(default=24, ge=1, le=48)


class FeatureRunResult(BaseModel):
    feature_run_id: uuid.UUID
    as_of: datetime
    rows: int
    db_queries: int
    duration_ms: float
    lag_resolution: str
    weather_fallbacks: int


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(application_name="feature-service")
    install_query_counter(engine)
    app = create_app("feature-service",
                     description="Builds forecast features from raw vendor data.")
    release = app.state.release
    resolution = release.flag("lag_resolution", "hourly")
    if resolution not in BUILDERS:
        raise RuntimeError(f"unknown lag_resolution flag {resolution!r}")
    builder = BUILDERS[resolution]
    log.info("feature builder selected", extra={"lag_resolution": resolution})
    initialize_counters(BUILDS, [{"lag_resolution": resolution, "status": s}
                                 for s in ("completed", "failed")])

    @app.post("/v1/feature-runs", response_model=FeatureRunResult, tags=["features"])
    def build(request: FeatureRunRequest) -> FeatureRunResult:
        as_of = floor_hour((request.as_of or datetime.now(UTC)).astimezone(UTC))
        run_id = uuid.uuid4()
        attrs = {"lag_resolution": resolution}
        try:
            with engine.begin() as conn:
                conn.execute(insert(feature_runs).values(
                    feature_run_id=run_id, as_of=as_of, horizon_hours=request.horizon_hours,
                    status="running", builder_version=release.version, lag_resolution=resolution,
                ))
        except Exception as exc:
            # A build that cannot even be recorded (e.g. the database refuses the connection)
            # is still a failed build.
            BUILDS.add(1, {**attrs, "status": "failed"})
            log.error("feature build failed", extra={
                "feature_run_id": str(run_id), "stage": "register",
                "error": f"{type(exc).__name__}: {exc}"[:2000]})
            raise HTTPException(503, f"feature build could not start: {type(exc).__name__}") from exc
        t0 = time.perf_counter()
        with tracer.start_as_current_span("build features", attributes={
            "gridcast.feature_run_id": str(run_id), "gridcast.lag_resolution": resolution,
            "gridcast.as_of": as_of.isoformat(),
        }) as span, QueryCount() as queries:
            try:
                with engine.connect() as conn:
                    rows, stats = builder(conn, as_of, request.horizon_hours)
                with engine.begin() as conn:
                    conn.execute(insert(forecast_features),
                                 [{"feature_run_id": run_id, **r.record()} for r in rows])
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
                BUILDS.add(1, {**attrs, "status": "failed"})
                BUILD_SECONDS.record(elapsed, {**attrs, "status": "failed"})
                DB_QUERIES.add(queries.count, attrs)
                log.error("feature build failed", extra={
                    "feature_run_id": str(run_id), "error": f"{type(exc).__name__}: {exc}"[:300],
                    "db_queries": queries.count,
                })
                with engine.begin() as conn:
                    conn.execute(update(feature_runs).where(
                        feature_runs.c.feature_run_id == run_id
                    ).values(status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
                             duration_ms=elapsed * 1000, db_queries=queries.count))
                code = 409 if isinstance(exc, MissingHistory) else 500
                raise HTTPException(code, f"feature build failed: {exc}") from exc
            elapsed = time.perf_counter() - t0
            span.set_attribute("gridcast.db_queries", queries.count)
            span.set_attribute("gridcast.rows", len(rows))
        with engine.begin() as conn:
            conn.execute(update(feature_runs).where(feature_runs.c.feature_run_id == run_id).values(
                status="completed", rows=len(rows), db_queries=queries.count,
                duration_ms=elapsed * 1000,
            ))
        BUILDS.add(1, {**attrs, "status": "completed"})
        BUILD_SECONDS.record(elapsed, {**attrs, "status": "completed"})
        DB_QUERIES.add(queries.count, attrs)
        log.info("feature build completed", extra={
            "feature_run_id": str(run_id), "rows": len(rows), "db_queries": queries.count,
            "duration_ms": round(elapsed * 1000, 1), **stats,
        })
        return FeatureRunResult(
            feature_run_id=run_id, as_of=as_of, rows=len(rows), db_queries=queries.count,
            duration_ms=round(elapsed * 1000, 1), lag_resolution=resolution,
            weather_fallbacks=stats["weather_fallbacks"],
        )

    @app.get("/v1/feature-runs/{run_id}", tags=["features"])
    def get_run(run_id: uuid.UUID) -> dict:
        with engine.connect() as conn:
            row = conn.execute(
                select(feature_runs).where(feature_runs.c.feature_run_id == run_id)
            ).mappings().first()
        if row is None:
            raise HTTPException(404, "unknown feature run")
        return dict(row)

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

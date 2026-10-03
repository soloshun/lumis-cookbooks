"""Forecast service: serves the registry's `production` model.

The model is resolved from the registry alias at startup and re-checked every
`FORECAST_MODEL_POLL_SECONDS`; when the alias moves, the new artifact is loaded and swapped in
without a restart (and logged). POST /v1/forecast-runs runs inference over a feature run and
stores probabilistic forecasts in `ml.forecasts`.
"""

import asyncio
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert, select

from gridcast.catalog import catalog
from gridcast.db.engine import make_engine
from gridcast.db.schema import forecast_features, forecast_runs, forecasts
from gridcast.features.engineering import MODEL_FEATURES, to_matrix
from gridcast.ml import registry
from gridcast.ml.artifacts import ArtifactStore
from gridcast.services.common import create_app, serve
from gridcast.telemetry import SECONDS_BUCKETS

log = logging.getLogger(__name__)
tracer = trace.get_tracer("gridcast.forecast")
meter = metrics.get_meter("gridcast.forecast")
INFERENCE_SECONDS = meter.create_histogram(
    "gridcast.inference.duration", unit="s", description="Model inference time per forecast run",
    explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
RUNS = meter.create_counter("gridcast.forecast.runs", description="Forecast runs by status")
MODEL_LOADS = meter.create_counter("gridcast.model.loads", description="Model (re)loads")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORECAST_", env_file=".env", extra="ignore")

    model_alias: str = "production"
    model_poll_seconds: int = 30


@dataclass
class LoadedModel:
    version: registry.ModelVersion
    estimator: Any
    loaded_at: float


class ForecastRunRequest(BaseModel):
    feature_run_id: uuid.UUID


class ForecastRunResult(BaseModel):
    forecast_run_id: uuid.UUID
    feature_run_id: uuid.UUID
    model_name: str
    model_version: int
    rows: int
    inference_ms: float


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(application_name="forecast-service")
    store = ArtifactStore()
    state: dict[str, LoadedModel] = {}
    lock = threading.Lock()

    def refresh() -> None:
        with engine.connect() as conn:
            resolved = registry.resolve(conn, settings.model_alias)
        if resolved is None:
            log.warning("no model behind alias", extra={"alias": settings.model_alias})
            return
        version, _ = resolved
        current = state.get("model")
        if current and current.version.version == version.version:
            return
        t0 = time.perf_counter()
        estimator = store.get_object(version.artifact_uri)
        if list(version.feature_names) != list(MODEL_FEATURES):
            raise RuntimeError("model feature contract does not match serving features")
        with lock:
            state["model"] = LoadedModel(version, estimator, time.time())
        MODEL_LOADS.add(1, {"model_version": str(version.version), "profile": version.profile})
        log.info("model loaded", extra={
            "model_name": version.model_name, "model_version": version.version,
            "profile": version.profile, "algorithm": version.algorithm,
            "previous_version": current.version.version if current else None,
            "load_ms": round((time.perf_counter() - t0) * 1000, 1),
        })

    async def poll() -> None:
        while True:
            await asyncio.sleep(settings.model_poll_seconds)
            try:
                await asyncio.to_thread(refresh)
            except Exception as exc:
                log.error("model refresh failed", extra={"error": f"{type(exc).__name__}: {exc}"})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for attempt in range(30):
            try:
                await asyncio.to_thread(refresh)
                break
            except Exception as exc:
                log.warning("initial model load failed; retrying", extra={
                    "attempt": attempt, "error": f"{type(exc).__name__}: {exc}"[:300]})
                await asyncio.sleep(5)
        task = asyncio.create_task(poll())
        yield
        task.cancel()
        engine.dispose()

    def readiness() -> tuple[bool, str]:
        model = state.get("model")
        return (model is not None, f"model v{model.version.version}" if model else "no model")

    app = create_app("forecast-service", lifespan=lifespan, readiness=readiness,
                     description="Serves probabilistic load forecasts from the production model.")
    release_version = app.state.release.version

    @app.get("/v1/models/active", tags=["models"])
    def active() -> dict:
        model = state.get("model")
        if model is None:
            raise HTTPException(503, "no model loaded")
        v = model.version
        return {"model_name": v.model_name, "version": v.version, "profile": v.profile,
                "algorithm": v.algorithm, "params": v.params, "metrics": v.metrics,
                "loaded_at": model.loaded_at}

    @app.post("/v1/forecast-runs", response_model=ForecastRunResult, tags=["forecasts"])
    def run(request: ForecastRunRequest) -> ForecastRunResult:
        with lock:
            model = state.get("model")
        if model is None:
            raise HTTPException(503, "no model loaded")
        with engine.connect() as conn:
            records = [dict(r) for r in conn.execute(select(forecast_features).where(
                forecast_features.c.feature_run_id == request.feature_run_id
            ).order_by(forecast_features.c.zone_id, forecast_features.c.target_ts)).mappings()]
        if not records:
            raise HTTPException(404, "feature run has no rows")
        labels = {"model_version": str(model.version.version), "profile": model.version.profile}
        with tracer.start_as_current_span("model inference", attributes={
            "gridcast.model_version": model.version.version,
            "gridcast.model_profile": model.version.profile, "gridcast.rows": len(records),
        }):
            t0 = time.perf_counter()
            quantiles = model.estimator.predict_quantiles(to_matrix(records))
            elapsed = time.perf_counter() - t0
        INFERENCE_SECONDS.record(elapsed, labels)
        cat = catalog()
        run_id = uuid.uuid4()
        rows = []
        for record, (p10, p50, p90) in zip(records, quantiles, strict=True):
            base = cat.zone(record["zone_id"]).base_load_mw
            rows.append({
                "forecast_run_id": run_id, "zone_id": record["zone_id"],
                "target_ts": record["target_ts"], "horizon_h": record["horizon_h"],
                "load_mw_p10": float(p10 * base), "load_mw_p50": float(p50 * base),
                "load_mw_p90": float(p90 * base),
            })
        with engine.begin() as conn:
            conn.execute(insert(forecast_runs).values(
                forecast_run_id=run_id, feature_run_id=request.feature_run_id,
                model_name=model.version.model_name, model_version=model.version.version,
                status="completed", rows=len(rows), inference_ms=elapsed * 1000,
                server_version=release_version,
            ))
            conn.execute(insert(forecasts), rows)
        RUNS.add(1, {**labels, "status": "completed"})
        log.info("forecast run completed", extra={
            "forecast_run_id": str(run_id), "feature_run_id": str(request.feature_run_id),
            "model_version": model.version.version, "rows": len(rows),
            "inference_ms": round(elapsed * 1000, 1),
        })
        return ForecastRunResult(
            forecast_run_id=run_id, feature_run_id=request.feature_run_id,
            model_name=model.version.model_name, model_version=model.version.version,
            rows=len(rows), inference_ms=round(elapsed * 1000, 1),
        )

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

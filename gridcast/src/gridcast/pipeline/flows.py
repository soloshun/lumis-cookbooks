"""Prefect flows: the forecast pipeline and model training.

forecast-pipeline (every FORECAST_INTERVAL_SECONDS):

    check inputs -> build features -> run forecast -> validate -> publish | hold

Services are called over HTTP (feature-service, forecast-service, planning-api); the pipeline
itself only reads the database for quality checks and writes `quality.*`. Each run is a Prefect
flow run (states, retries and logs visible in the Prefect UI) and an OpenTelemetry trace that
spans every service it touches.
"""

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
from opentelemetry import metrics, trace
from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert

from gridcast.db.engine import make_engine
from gridcast.db.schema import check_results, forecast_validations
from gridcast.quality import checks
from gridcast.telemetry import SECONDS_BUCKETS

log = logging.getLogger(__name__)
tracer = trace.get_tracer("gridcast.pipeline")
meter = metrics.get_meter("gridcast.pipeline")
RUN_SECONDS = meter.create_histogram(
    "gridcast.pipeline.run.duration", unit="s", description="End-to-end forecast pipeline duration",
    explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
RUNS = meter.create_counter("gridcast.pipeline.runs", description="Pipeline runs by outcome")
STAGE_SECONDS = meter.create_histogram(
    "gridcast.pipeline.stage.duration", unit="s", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS)
CHECKS = meter.create_counter("gridcast.quality.checks", description="Quality check results")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=".env", extra="ignore")

    feature_service_url: str = "http://localhost:8082"
    forecast_service_url: str = "http://localhost:8081"
    planning_api_url: str = "http://localhost:8080"
    horizon_hours: int = 24
    request_timeout_seconds: float = 120.0
    interval_seconds: int = 300


_engine = None


def _db():
    global _engine
    if _engine is None:
        _engine = make_engine(application_name="forecast-pipeline")
    return _engine


@contextmanager
def _stage(name: str) -> Iterator[None]:
    """Time a pipeline stage as a span plus a duration metric labelled with its outcome."""
    t0 = time.perf_counter()
    status = "ok"
    with tracer.start_as_current_span(f"stage {name}"):
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            STAGE_SECONDS.record(time.perf_counter() - t0, {"stage": name, "status": status})


def _post(url: str, payload: dict, timeout: float) -> dict:
    response = httpx.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


@task(name="check-inputs")
def check_inputs(pipeline_run_id: str) -> list[checks.CheckResult]:
    with _stage("check_inputs"), _db().connect() as conn:
        results = checks.input_checks(conn)
    _store(pipeline_run_id, results)
    return results


@task(name="build-features", retries=2, retry_delay_seconds=10)
def build_features(settings: Settings) -> dict:
    with _stage("build_features"):
        return _post(f"{settings.feature_service_url}/v1/feature-runs",
                     {"horizon_hours": settings.horizon_hours}, settings.request_timeout_seconds)


@task(name="run-forecast", retries=2, retry_delay_seconds=10)
def run_forecast(settings: Settings, feature_run_id: str) -> dict:
    with _stage("run_forecast"):
        return _post(f"{settings.forecast_service_url}/v1/forecast-runs",
                     {"feature_run_id": feature_run_id}, settings.request_timeout_seconds)


@task(name="validate-forecast")
def validate_forecast(settings: Settings, pipeline_run_id: str, forecast_run_id: str,
                      inputs: list[checks.CheckResult]) -> tuple[str, list[str], list[str]]:
    with _stage("validate"), _db().begin() as conn:
        results = checks.forecast_checks(conn, forecast_run_id, settings.horizon_hours)
        decision, failed, warned = checks.decide([*inputs, *results])
        conn.execute(insert(forecast_validations).values(
            validation_id=uuid.uuid4(), forecast_run_id=forecast_run_id,
            pipeline_run_id=pipeline_run_id, decision=decision, failed_checks=failed,
            warnings=warned,
        ))
    _store(pipeline_run_id, results)
    return decision, failed, warned


@task(name="publish-plan", retries=2, retry_delay_seconds=5)
def publish_plan(settings: Settings, forecast_run_id: str, pipeline_run_id: str) -> dict:
    with _stage("publish"):
        return _post(f"{settings.planning_api_url}/v1/plans",
                     {"forecast_run_id": forecast_run_id,
                      "published_by": f"forecast-pipeline/{pipeline_run_id}"}, 30)


def _store(pipeline_run_id: str, results: list[checks.CheckResult]) -> None:
    with _db().begin() as conn:
        conn.execute(insert(check_results),
                     [{"pipeline_run_id": pipeline_run_id, **r.record()} for r in results])
    for r in results:
        CHECKS.add(1, {"check": r.check_name, "status": r.status})


@flow(name="forecast-pipeline", log_prints=True, timeout_seconds=600)
def forecast_pipeline() -> dict:
    settings = Settings()
    logger = get_run_logger()
    pipeline_run_id = str(flow_run.get_id() or uuid.uuid4())
    t0 = time.perf_counter()
    outcome = "failed"
    with tracer.start_as_current_span("forecast-pipeline", attributes={
        "prefect.flow_run_id": pipeline_run_id,
    }) as span:
        try:
            inputs = check_inputs(pipeline_run_id)
            features = build_features(settings)
            logger.info("features built: %s rows, %s queries, %.0f ms", features["rows"],
                        features["db_queries"], features["duration_ms"])
            forecast = run_forecast(settings, features["feature_run_id"])
            logger.info("forecast run %s with model v%s in %.0f ms", forecast["forecast_run_id"],
                        forecast["model_version"], forecast["inference_ms"])
            decision, failed, warned = validate_forecast(
                settings, pipeline_run_id, forecast["forecast_run_id"], inputs)
            if warned:
                logger.warning("quality warnings: %s", ", ".join(warned))
            if decision == "publish":
                publish_plan(settings, forecast["forecast_run_id"], pipeline_run_id)
                outcome = "published"
            else:
                logger.error("forecast held by validation gate: %s", ", ".join(failed))
                outcome = "held"
            return {"outcome": outcome, "forecast_run_id": forecast["forecast_run_id"],
                    "failed_checks": failed, "warnings": warned}
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
            raise
        finally:
            elapsed = time.perf_counter() - t0
            span.set_attribute("gridcast.outcome", outcome)
            RUN_SECONDS.record(elapsed, {"status": outcome})
            RUNS.add(1, {"status": outcome})
            log.info("pipeline run finished", extra={
                "outcome": outcome, "duration_s": round(elapsed, 2),
                "pipeline_run_id": pipeline_run_id,
            })


@flow(name="train-model", log_prints=True)
def train_model(profile: str = "standard", promote_alias: str | None = None,
                max_train_rows: int | None = None, actor: str = "ml-platform") -> dict:
    from gridcast.config import DatabaseSettings
    from gridcast.ml.artifacts import ArtifactStore
    from gridcast.ml.training import TrainingConfig, train

    store = ArtifactStore()
    store.ensure_bucket()
    engine = make_engine(DatabaseSettings(), application_name="train-model")
    result = train(engine, TrainingConfig(profile=profile, max_train_rows=max_train_rows),
                   store=store, actor=actor, promote_alias=promote_alias)
    get_run_logger().info("registered %s model v%s: %s", profile, result["version"],
                          result["metrics"])
    return result


def run_forever() -> None:
    """Long-lived pipeline worker: run the forecast flow on a fixed cadence, in-process.

    Running in-process (instead of a subprocess per run) keeps one OpenTelemetry meter
    provider alive, so pipeline counters and histograms are continuous series.
    """
    settings = Settings()
    log.info("pipeline worker started", extra={"interval_seconds": settings.interval_seconds})
    while True:
        started = time.monotonic()
        try:
            forecast_pipeline()
        except Exception as exc:
            log.error("forecast pipeline run failed",
                      extra={"error": f"{type(exc).__name__}: {exc}"[:400],
                             "at": datetime.now(UTC).isoformat()})
        time.sleep(max(5.0, settings.interval_seconds - (time.monotonic() - started)))

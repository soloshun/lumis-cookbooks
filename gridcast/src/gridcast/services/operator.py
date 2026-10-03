"""Synthetic grid operator: the downstream consumer whose experience defines the SLOs.

Every minute it fetches the current dispatch plan (as a control-room tool would) and every five
minutes the realized forecast accuracy. It exports what the business cares about:

    gridcast_consumer_plan_age_seconds      how old the plan in use is
    gridcast_consumer_forecast_mape_ratio   rolling 6 h MAPE of published forecasts
    gridcast_consumer_coverage_ratio        share of actuals inside the p10-p90 band
    gridcast_consumer_requests_total        planning-api calls by endpoint/status/outcome
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from opentelemetry import metrics
from opentelemetry.metrics import Observation
from pydantic_settings import BaseSettings, SettingsConfigDict

from gridcast.services.common import create_app, serve

log = logging.getLogger(__name__)
meter = metrics.get_meter("gridcast.operator")
REQUESTS = meter.create_counter("gridcast.consumer.requests", description="Planning API calls")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPERATOR_", env_file=".env", extra="ignore")

    planning_api_url: str = "http://localhost:8080"
    poll_seconds: int = 60
    accuracy_every_polls: int = 5
    accuracy_window_hours: int = 6


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    observed: dict[str, float] = {}

    meter.create_observable_gauge(
        "gridcast.consumer.plan.age", unit="s", description="Age of the plan in use",
        callbacks=[lambda _: [Observation(observed["plan_age"])] if "plan_age" in observed else []],
    )
    meter.create_observable_gauge(
        "gridcast.consumer.forecast.mape", unit="1", description="Rolling realized MAPE",
        callbacks=[lambda _: [Observation(observed["mape"])] if "mape" in observed else []],
    )
    meter.create_observable_gauge(
        "gridcast.consumer.coverage", unit="1", description="Realized p10-p90 coverage",
        callbacks=[lambda _: [Observation(observed["coverage"])] if "coverage" in observed else []],
    )

    async def get(client: httpx.AsyncClient, path: str, **params) -> dict | None:
        try:
            response = await client.get(f"{settings.planning_api_url}{path}", params=params)
            outcome = "ok" if response.is_success else "http_error"
            REQUESTS.add(1, {"endpoint": path, "status": str(response.status_code),
                             "outcome": outcome})
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            if not isinstance(exc, httpx.HTTPStatusError):
                REQUESTS.add(1, {"endpoint": path, "status": "transport_error",
                                 "outcome": "transport_error"})
            log.warning("planning api call failed", extra={"endpoint": path, "error": str(exc)[:200]})
            return None

    async def loop() -> None:
        tick = 0
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                plan = await get(client, "/v1/plans/current")
                if plan is not None:
                    observed["plan_age"] = float(plan["age_seconds"])
                    if plan["age_seconds"] > 900:
                        log.warning("operating on a stale dispatch plan", extra={
                            "plan_id": plan["plan_id"], "age_seconds": plan["age_seconds"]})
                if tick % settings.accuracy_every_polls == 0:
                    acc = await get(client, "/v1/accuracy", hours=settings.accuracy_window_hours)
                    if acc and acc.get("mape") is not None:
                        observed["mape"] = float(acc["mape"])
                        observed["coverage"] = float(acc["coverage_p10_p90"])
                        log.info("forecast accuracy", extra={
                            "mape": acc["mape"], "coverage": acc["coverage_p10_p90"],
                            "samples": acc["samples"]})
                tick += 1
                await asyncio.sleep(settings.poll_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    from gridcast.telemetry import initialize_counters

    app = create_app("grid-operator", lifespan=lifespan,
                     description="Synthetic downstream consumer of dispatch plans.")

    initialize_counters(REQUESTS, [
        {"endpoint": endpoint, "status": "transport_error", "outcome": "transport_error"}
        for endpoint in ("/v1/plans/current", "/v1/accuracy")
    ])

    @app.get("/status", tags=["ops"])
    def status() -> dict:
        return observed

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

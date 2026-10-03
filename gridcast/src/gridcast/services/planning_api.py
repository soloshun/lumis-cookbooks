"""Planning API: turns validated forecasts into dispatch plans for grid operators.

POST /v1/plans                 publish a plan from a validated forecast run (pipeline only)
GET  /v1/plans/current         the plan operators should be using, with its age
GET  /v1/forecasts/current     per-zone hourly forecast behind the current plan
GET  /v1/accuracy              realized accuracy of what was published (MAPE / coverage)
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query
from opentelemetry import metrics, trace
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert, select, text, update

from gridcast.db.engine import make_engine
from gridcast.db.schema import dispatch_plans, forecast_runs, forecasts, plan_intervals
from gridcast.services.common import create_app, serve

log = logging.getLogger(__name__)
tracer = trace.get_tracer("gridcast.planning")
meter = metrics.get_meter("gridcast.planning")
PLANS = meter.create_counter("gridcast.plans.published", description="Dispatch plans published")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLANNING_", env_file=".env", extra="ignore")

    reserve_margin_ratio: float = 0.03


class PublishRequest(BaseModel):
    forecast_run_id: uuid.UUID
    published_by: str


ACCURACY_SQL = text("""
WITH hours AS (
    SELECT generate_series(date_trunc('hour', now()) - make_interval(hours => :hours),
                           date_trunc('hour', now()) - interval '1 hour',
                           interval '1 hour') AS hour
),
actual AS (
    SELECT zone_id, date_trunc('hour', ts) AS hour, avg(load_mw) AS load_mw, count(*) AS n
    FROM raw.demand_readings
    WHERE ts >= date_trunc('hour', now()) - make_interval(hours => :hours)
      AND ts < date_trunc('hour', now())
    GROUP BY 1, 2
),
in_effect AS (
    SELECT h.hour, p.forecast_run_id
    FROM hours h
    JOIN LATERAL (
        SELECT forecast_run_id FROM planning.dispatch_plans
        WHERE published_at <= h.hour ORDER BY published_at DESC LIMIT 1
    ) p ON true
)
SELECT f.zone_id, e.hour, f.load_mw_p10, f.load_mw_p50, f.load_mw_p90, a.load_mw AS actual
FROM in_effect e
JOIN ml.forecasts f ON f.forecast_run_id = e.forecast_run_id AND f.target_ts = e.hour
JOIN actual a ON a.zone_id = f.zone_id AND a.hour = e.hour AND a.n >= 50
""")


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(application_name="planning-api")
    app = create_app("planning-api",
                     description="Publishes dispatch plans and serves them to operators.")

    @app.post("/v1/plans", tags=["plans"], status_code=201)
    def publish(request: PublishRequest) -> dict:
        with engine.begin() as conn:
            run = conn.execute(select(forecast_runs).where(
                forecast_runs.c.forecast_run_id == request.forecast_run_id
            )).mappings().first()
            if run is None or run["status"] != "completed":
                raise HTTPException(409, "forecast run missing or not completed")
            rows = conn.execute(select(forecasts).where(
                forecasts.c.forecast_run_id == request.forecast_run_id
            )).mappings().all()
            if not rows:
                raise HTTPException(409, "forecast run has no forecasts")
            intervals = []
            system: dict[datetime, float] = {}
            for r in rows:
                reserve = max(0.0, r["load_mw_p90"] - r["load_mw_p50"]) + (
                    settings.reserve_margin_ratio * r["load_mw_p50"])
                intervals.append({
                    "zone_id": r["zone_id"], "interval_start": r["target_ts"],
                    "forecast_load_mw": r["load_mw_p50"], "reserve_mw": reserve,
                    "scheduled_generation_mw": r["load_mw_p50"] + reserve,
                })
                system[r["target_ts"]] = system.get(r["target_ts"], 0.0) + r["load_mw_p50"]
            plan_id = uuid.uuid4()
            conn.execute(update(dispatch_plans).where(dispatch_plans.c.status == "active")
                         .values(status="superseded"))
            conn.execute(insert(dispatch_plans).values(
                plan_id=plan_id, forecast_run_id=request.forecast_run_id, status="active",
                valid_from=min(system), valid_to=max(system),
                total_energy_mwh=sum(system.values()), peak_load_mw=max(system.values()),
                published_by=request.published_by,
            ))
            conn.execute(insert(plan_intervals), [{"plan_id": plan_id, **i} for i in intervals])
        PLANS.add(1)
        log.info("dispatch plan published", extra={
            "plan_id": str(plan_id), "forecast_run_id": str(request.forecast_run_id),
            "peak_load_mw": round(max(system.values()), 1), "published_by": request.published_by,
        })
        return {"plan_id": str(plan_id), "intervals": len(intervals)}

    @app.get("/v1/plans/current", tags=["plans"])
    def current_plan() -> dict:
        with engine.connect() as conn:
            plan = conn.execute(select(dispatch_plans).where(dispatch_plans.c.status == "active")
                                .order_by(dispatch_plans.c.published_at.desc()).limit(1)
                                ).mappings().first()
            if plan is None:
                raise HTTPException(404, "no plan published yet")
            hourly = conn.execute(text("""
                SELECT interval_start, sum(forecast_load_mw) AS load_mw,
                       sum(reserve_mw) AS reserve_mw, count(*) AS zones
                FROM planning.plan_intervals WHERE plan_id = :plan_id
                GROUP BY interval_start ORDER BY interval_start
            """), {"plan_id": plan["plan_id"]}).mappings().all()
        age = (datetime.now(UTC) - plan["published_at"]).total_seconds()
        return {**dict(plan), "age_seconds": round(age, 1),
                "system_hourly": [dict(h) for h in hourly]}

    @app.get("/v1/forecasts/current", tags=["forecasts"])
    def current_forecast(zone_id: str | None = None) -> dict:
        with engine.connect() as conn:
            plan = conn.execute(select(dispatch_plans.c.forecast_run_id, dispatch_plans.c.plan_id)
                                .where(dispatch_plans.c.status == "active")
                                .order_by(dispatch_plans.c.published_at.desc()).limit(1)).first()
            if plan is None:
                raise HTTPException(404, "no plan published yet")
            query = select(forecasts).where(forecasts.c.forecast_run_id == plan.forecast_run_id)
            if zone_id:
                query = query.where(forecasts.c.zone_id == zone_id)
            rows = conn.execute(query.order_by(forecasts.c.zone_id, forecasts.c.target_ts)
                                ).mappings().all()
        return {"plan_id": str(plan.plan_id), "forecast_run_id": str(plan.forecast_run_id),
                "forecasts": [dict(r) for r in rows]}

    @app.get("/v1/accuracy", tags=["forecasts"])
    def accuracy(hours: int = Query(default=6, ge=1, le=168)) -> dict:
        with engine.connect() as conn:
            rows = conn.execute(ACCURACY_SQL, {"hours": hours}).mappings().all()
        if not rows:
            return {"hours": hours, "samples": 0, "mape": None, "coverage_p10_p90": None,
                    "by_zone": {}}
        by_zone: dict[str, list[float]] = {}
        covered = 0
        for r in rows:
            err = abs(r["actual"] - r["load_mw_p50"]) / r["actual"]
            by_zone.setdefault(r["zone_id"], []).append(err)
            covered += r["load_mw_p10"] <= r["actual"] <= r["load_mw_p90"]
        all_errors = [e for errors in by_zone.values() for e in errors]
        return {
            "hours": hours, "samples": len(rows),
            "mape": round(sum(all_errors) / len(all_errors), 5),
            "coverage_p10_p90": round(covered / len(rows), 4),
            "by_zone": {z: round(sum(e) / len(e), 5) for z, e in by_zone.items()},
        }

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

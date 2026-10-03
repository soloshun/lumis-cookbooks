"""Grid telemetry historian simulator (namespace `vendors`).

Stands in for the transmission operator's SCADA/AMI historian that publishes one-minute zone
demand. Demand is generated from the *true* weather (not from any weather vendor), so when a
weather vendor degrades, reality and GridCast's view of it genuinely diverge.

Supported vendor faults:
  outage        503 for every data request
  slow          added latency
  gap           data stops advancing (the historian's export job is stuck)
  unit_change   values silently switch from MW to kW (an unannounced API change)
  schema_break  `load_mw` is renamed to `demand_kw` (API v2 shipped without notice)
"""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict

from gridcast.catalog import catalog
from gridcast.demand.model import demand_mw
from gridcast.services.common import create_app, serve
from gridcast.services.vendor_faults import Faults, admin_router
from gridcast.weather.truth import TruthWeather

STEP = timedelta(minutes=1)
MAX_RANGE = timedelta(days=2)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRID_TELEMETRY_", env_file=".env", extra="ignore")

    truth_mode: str = "hybrid"
    admin_token: str = ""
    # Readings are published with a small delay, like a real historian export.
    publish_delay_seconds: int = 30


def create(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    truth = TruthWeather(mode=settings.truth_mode)
    faults = Faults({"outage", "slow", "gap", "unit_change", "schema_break"})
    app = create_app("grid-telemetry",
        description="Simulated grid SCADA/AMI historian publishing zone demand (external).",
    )
    app.include_router(admin_router(faults, settings.admin_token))
    cat = catalog()

    def reading(zone_id: str, ts: datetime) -> dict:
        zone = cat.zone(zone_id)
        st = cat.station(zone.station_id)
        weather, _ = truth.at(st.latitude, st.longitude, ts)
        value = round(demand_mw(zone, ts, weather), 3)
        fault = faults.state
        affected = fault.since is not None and ts >= fault.since
        if affected and fault.mode == "unit_change":
            value = round(value * 1000, 1)
        if affected and fault.mode == "schema_break":
            return {"zone_id": zone_id, "ts": ts, "demand_kw": round(value * 1000, 1),
                    "quality": "good"}
        return {"zone_id": zone_id, "ts": ts, "load_mw": value, "quality": "good"}

    def horizon() -> datetime:
        now = datetime.now(UTC) - timedelta(seconds=settings.publish_delay_seconds)
        fault = faults.state
        if fault.mode == "gap" and fault.since:
            now = min(now, fault.since)
        return datetime.fromtimestamp(now.timestamp() // 60 * 60, UTC)

    @app.middleware("http")
    async def transport_faults(request, call_next):  # noqa: ANN001
        if request.url.path.startswith("/v1/"):
            response = await faults.apply_transport_faults()
            if response is not None:
                return response
        return await call_next(request)

    @app.get("/v1/zones", tags=["data"])
    def zones() -> list[dict]:
        return [z.model_dump() for z in cat.zones]

    @app.get("/v1/load", tags=["data"])
    def load(zone_id: str, start: datetime, end: datetime | None = None) -> JSONResponse:
        try:
            cat.zone(zone_id)
        except StopIteration:
            raise HTTPException(404, f"unknown zone {zone_id}") from None
        last = horizon()
        end = min(end or last, last)
        if start.tzinfo is None or end - start > MAX_RANGE:
            raise HTTPException(422, "start must be timezone-aware and range <= 2 days")
        ts = datetime.fromtimestamp(-(-start.timestamp() // 60) * 60, UTC)
        rows = []
        while ts <= end:
            row = reading(zone_id, ts)
            row["ts"] = ts.isoformat()
            rows.append(row)
            ts += STEP
        return JSONResponse({"zone_id": zone_id, "readings": rows, "api_version": (
            "2.0" if faults.state.mode == "schema_break" else "1.4")})

    @app.get("/v1/load/latest", tags=["data"])
    def latest(zone_id: str) -> dict:
        ts = horizon()
        row = reading(zone_id, ts)
        row["ts"] = ts.isoformat()
        return row

    return app


def main() -> None:
    settings = Settings()
    serve(create(settings))

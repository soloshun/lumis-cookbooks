from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gridcast.services import grid_telemetry, weather_vendor
from gridcast.services.ingestion import DemandIn, ObservationIn

ADMIN = {"x-vendor-admin-token": "secret"}


@pytest.fixture
def weather():
    return TestClient(weather_vendor.create(weather_vendor.Settings(
        admin_token="secret", truth_mode="synthetic")))


@pytest.fixture
def grid():
    return TestClient(grid_telemetry.create(grid_telemetry.Settings(
        admin_token="secret", truth_mode="synthetic")))


def test_observations_match_ingestion_contract(weather):
    start = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    rows = weather.get("/v1/observations", params={"station_id": "st-accra", "start": start}).json()
    assert 20 <= len(rows) <= 25
    ObservationIn.model_validate(rows[0])


def test_admin_api_requires_token(weather):
    assert weather.post("/admin/faults", json={"mode": "outage"}).status_code == 401


def test_outage_returns_503_and_clears(weather):
    weather.post("/admin/faults", json={"mode": "outage"}, headers=ADMIN)
    assert weather.get("/v1/observations/latest", params={"station_id": "st-accra"}).status_code == 503
    weather.delete("/admin/faults", headers=ADMIN)
    assert weather.get("/v1/observations/latest", params={"station_id": "st-accra"}).status_code == 200


def test_stale_mode_freezes_values_but_not_timestamps(weather):
    since = datetime.now(UTC) - timedelta(minutes=40)
    weather.post("/admin/faults", json={"mode": "stale", "since": since.isoformat()}, headers=ADMIN)
    rows = weather.get("/v1/observations", params={
        "station_id": "st-tamale", "start": (since + timedelta(minutes=5)).isoformat()}).json()
    values = {(r["temperature_c"], r["cloud_cover_pct"]) for r in rows}
    stamps = {r["observed_at"] for r in rows}
    assert len(rows) >= 6 and len(values) == 1 and len(stamps) == len(rows)


def test_unsupported_fault_rejected(grid):
    assert grid.post("/admin/faults", json={"mode": "stale"}, headers=ADMIN).status_code == 400


def test_demand_contract_and_schema_break(grid):
    start = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    payload = grid.get("/v1/load", params={"zone_id": "zone-accra", "start": start}).json()
    DemandIn.model_validate(payload["readings"][0])
    since = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    grid.post("/admin/faults", json={"mode": "schema_break", "since": since}, headers=ADMIN)
    payload = grid.get("/v1/load/latest", params={"zone_id": "zone-accra"}).json()
    assert "demand_kw" in payload and "load_mw" not in payload
    with pytest.raises(ValidationError):
        DemandIn.model_validate(payload)


def test_unit_change_multiplies_values(grid):
    before = grid.get("/v1/load/latest", params={"zone_id": "zone-tamale"}).json()["load_mw"]
    since = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    grid.post("/admin/faults", json={"mode": "unit_change", "since": since}, headers=ADMIN)
    after = grid.get("/v1/load/latest", params={"zone_id": "zone-tamale"}).json()["load_mw"]
    assert before < 1000 and after > 100 * before

"""Data-quality and forecast-validation checks.

Checks are deliberately ordinary — the kind of guards a real team writes — and each has a
severity: `fail` checks hold the forecast (the previous plan stays in force), `warn` checks
are recorded but do not block publication. Several realistic incidents therefore *pass* the
gate and only show up downstream (e.g. stale-but-fresh-looking vendor data degrades accuracy
while only raising a warning), which is exactly the ambiguity a diagnosis system must handle.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy import Connection, text

from gridcast.catalog import catalog

Status = Literal["pass", "warn", "fail"]


@dataclass
class CheckResult:
    check_name: str
    subject: str
    status: Status
    observed: float | None = None
    threshold: float | None = None
    details: dict = field(default_factory=dict)

    def record(self) -> dict:
        return asdict(self)


def _grade(value: float | None, warn: float, fail: float, *, higher_is_worse: bool = True
           ) -> Status:
    if value is None:
        return "fail"
    if higher_is_worse:
        return "fail" if value > fail else "warn" if value > warn else "pass"
    return "fail" if value < fail else "warn" if value < warn else "pass"


def input_checks(conn: Connection) -> list[CheckResult]:
    results = []
    freshness = {
        "weather_observations": ("SELECT extract(epoch FROM now() - max(observed_at)) "
                                 "FROM raw.weather_observations", 600, 1200),
        "weather_forecasts": ("SELECT extract(epoch FROM now() - max(issued_at)) "
                              "FROM raw.weather_forecasts", 7200, 10800),
        "demand": ("SELECT extract(epoch FROM now() - max(ts)) FROM raw.demand_readings",
                   300, 900),
    }
    for dataset, (sql, warn, fail) in freshness.items():
        age = conn.execute(text(sql)).scalar()
        age = float(age) if age is not None else None
        results.append(CheckResult(f"freshness.{dataset}", dataset, _grade(age, warn, fail),
                                   age, fail, {"unit": "seconds", "warn_at": warn}))

    # Vendor values vary from one 5-minute observation to the next; identical readings across
    # half an hour mean the feed is repeating itself even if timestamps look fresh.
    rows = conn.execute(text("""
        SELECT station_id, count(*) AS n,
               count(DISTINCT (temperature_c, relative_humidity_pct, cloud_cover_pct)) AS distinct_n
        FROM raw.weather_observations
        WHERE observed_at > now() - interval '30 minutes'
        GROUP BY station_id
    """)).mappings().all()
    for r in rows:
        status: Status = "warn" if r["n"] >= 5 and r["distinct_n"] <= 1 else "pass"
        results.append(CheckResult("variability.weather_observations", r["station_id"], status,
                                   float(r["distinct_n"]), 1.0, {"observations": r["n"]}))

    cat = catalog()
    rows = conn.execute(text("""
        SELECT zone_id, avg(load_mw) AS mean_load, count(*) AS n
        FROM raw.demand_readings WHERE ts > now() - interval '60 minutes'
        GROUP BY zone_id
    """)).mappings().all()
    seen = set()
    for r in rows:
        seen.add(r["zone_id"])
        ratio = float(r["mean_load"]) / cat.zone(r["zone_id"]).base_load_mw
        status = "fail" if not 0.3 <= ratio <= 2.5 else "pass"
        results.append(CheckResult("range.demand", r["zone_id"], status, ratio, 2.5,
                                   {"lower": 0.3, "mean_load_mw": float(r["mean_load"])}))
        results.append(CheckResult("completeness.demand", r["zone_id"],
                                   _grade(float(r["n"]), 45, 20, higher_is_worse=False),
                                   float(r["n"]), 45.0, {"expected": 60}))
    for zone in cat.zones:
        if zone.id not in seen:
            results.append(CheckResult("completeness.demand", zone.id, "fail", 0.0, 45.0,
                                       {"expected": 60}))
    return results


def forecast_checks(conn: Connection, forecast_run_id: str, horizon: int) -> list[CheckResult]:
    cat = catalog()
    results = []
    expected = len(cat.zones) * horizon
    rows = conn.execute(text("""
        SELECT zone_id, target_ts, load_mw_p10, load_mw_p50, load_mw_p90
        FROM ml.forecasts WHERE forecast_run_id = :id
    """), {"id": forecast_run_id}).mappings().all()
    results.append(CheckResult("completeness.forecast", forecast_run_id,
                               "pass" if len(rows) == expected else "fail",
                               float(len(rows)), float(expected)))
    worst = 0.0
    out_of_range = 0
    for r in rows:
        ratio = r["load_mw_p50"] / cat.zone(r["zone_id"]).base_load_mw
        worst = max(worst, abs(ratio - 1))
        out_of_range += not 0.3 <= ratio <= 2.5
    results.append(CheckResult("range.forecast", forecast_run_id,
                               "fail" if out_of_range else "pass", float(out_of_range), 0.0,
                               {"max_relative_deviation": worst}))

    change = conn.execute(text("""
        WITH prev AS (
            SELECT forecast_run_id FROM planning.dispatch_plans
            ORDER BY published_at DESC LIMIT 1
        )
        SELECT avg(abs(n.load_mw_p50 - o.load_mw_p50) / nullif(o.load_mw_p50, 0))
        FROM ml.forecasts n
        JOIN ml.forecasts o ON o.forecast_run_id = (SELECT forecast_run_id FROM prev)
             AND o.zone_id = n.zone_id AND o.target_ts = n.target_ts
        WHERE n.forecast_run_id = :id
    """), {"id": forecast_run_id}).scalar()
    change = float(change) if change is not None else None
    results.append(CheckResult("stability.forecast_vs_published", forecast_run_id,
                               "pass" if change is None else _grade(change, 0.10, 10.0),
                               change, 0.10, {"meaning": "mean relative change vs current plan"}))
    return results


def decide(results: list[CheckResult]) -> tuple[str, list[str], list[str]]:
    failed = sorted({f"{r.check_name}:{r.subject}" for r in results if r.status == "fail"})
    warned = sorted({f"{r.check_name}:{r.subject}" for r in results if r.status == "warn"})
    return ("hold" if failed else "publish"), failed, warned

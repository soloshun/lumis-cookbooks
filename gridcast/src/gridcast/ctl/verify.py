"""`gridcastctl verify`: prove every layer of the estate works, end to end.

Each check reads from the same interfaces an external observer (and later Lumis) would use:
the Kubernetes API, service HTTP APIs, Prometheus, Loki, Tempo, Prefect and PostgreSQL.
"""

import time
from collections.abc import Callable

import httpx
from rich.table import Table

from gridcast.ctl import estate
from gridcast.ctl.shell import console, env


def _prom(query: str) -> list:
    port = env()["PROMETHEUS_PORT"]
    r = httpx.get(f"http://localhost:{port}/api/v1/query", params={"query": query}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["result"]


def _loki_count(selector: str, minutes: int = 15) -> int:
    port = env()["LOKI_PORT"]
    now = time.time_ns()
    r = httpx.get(f"http://localhost:{port}/loki/api/v1/query_range", params={
        "query": selector, "start": now - minutes * 60 * 10**9, "end": now, "limit": 50,
    }, timeout=10)
    r.raise_for_status()
    return sum(len(s["values"]) for s in r.json()["data"]["result"])


def _tempo_traces(service: str) -> int:
    port = env()["TEMPO_PORT"]
    r = httpx.get(f"http://localhost:{port}/api/search", params={
        "tags": f"service.name={service}", "limit": 20,
        "start": int(time.time()) - 1800, "end": int(time.time()),
    }, timeout=10)
    r.raise_for_status()
    return len(r.json().get("traces", []))


def _prefect_completed() -> int:
    port = env()["PREFECT_PORT"]
    r = httpx.post(f"http://localhost:{port}/api/flow_runs/count", json={
        "flow_runs": {"state": {"type": {"any_": ["COMPLETED"]}}}}, timeout=10)
    r.raise_for_status()
    return int(r.json())


def checks() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    def pods():
        rows = estate.pods()
        bad = [p["name"] for p in rows if not p["ready"]]
        return (bool(rows) and not bad, f"{len(rows)} pods" + (f", not ready: {bad}" if bad else ""))

    def plan():
        p = estate.http_json("http://localhost:8080/v1/plans/current")
        if not p:
            return False, "no plan"
        return p["age_seconds"] < 900, f"age {p['age_seconds']:.0f}s"

    def model():
        m = estate.http_json("http://localhost:8081/v1/models/active")
        return (m is not None, f"v{m['version']} ({m['profile']})" if m else "none loaded")

    def db():
        out = estate.psql("SELECT (SELECT count(*) FROM raw.demand_readings), "
                          "(SELECT count(*) FROM raw.weather_observations), "
                          "(SELECT count(*) FROM ml.forecast_runs), "
                          "(SELECT count(*) FROM planning.dispatch_plans)").strip().split("|")
        ok = all(int(x) > 0 for x in out)
        return ok, f"demand={out[0]} weather={out[1]} forecast_runs={out[2]} plans={out[3]}"

    def prom_app():
        series = _prom("gridcast_pipeline_runs_total")
        return bool(series), f"{len(series)} pipeline series"

    def prom_k8s():
        a = _prom('k8s_container_restarts{k8s_namespace_name="gridcast"}')
        b = _prom('container_cpu_cfs_periods_total{namespace="gridcast"}')
        return bool(a) and bool(b), f"k8s_cluster={len(a)} cadvisor={len(b)}"

    def prom_db():
        series = _prom('pg_up')
        return bool(series) and series[0]["value"][1] == "1", "postgres-exporter"

    def prom_graph():
        series = _prom("traces_service_graph_request_total")
        return bool(series), f"{len(series)} service-graph edges"

    def loki():
        n = _loki_count('{service_name="feature-service"}')
        m = _loki_count('{k8s_namespace_name="gridcast"} |= "Scheduled"', minutes=180)
        return n > 0, f"feature-service lines={n}, k8s events seen={m > 0}"

    def tempo():
        n = _tempo_traces("forecast-pipeline")
        return n > 0, f"{n} recent pipeline traces"

    def prefect():
        n = _prefect_completed()
        return n > 0, f"{n} completed flow runs"

    def uis():
        values = env()
        codes = {}
        for name, port in (("grafana", values["GRAFANA_PORT"]), ("pgadmin", values["PGADMIN_PORT"])):
            try:
                codes[name] = httpx.get(f"http://localhost:{port}/", timeout=10,
                                        follow_redirects=True).status_code
            except httpx.HTTPError:
                codes[name] = 0
        return all(c == 200 for c in codes.values()), str(codes)

    return [
        ("Kubernetes pods ready", pods), ("Dispatch plan fresh", plan),
        ("Production model loaded", model), ("Database populated", db),
        ("Metrics: application", prom_app), ("Metrics: Kubernetes", prom_k8s),
        ("Metrics: PostgreSQL", prom_db), ("Metrics: trace service graph", prom_graph),
        ("Logs in Loki", loki), ("Traces in Tempo", tempo), ("Prefect flow runs", prefect),
        ("Grafana + pgAdmin reachable", uis),
    ]


def run_checks() -> bool:
    table = Table("check", "result", "detail")
    all_ok = True
    for name, fn in checks():
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"[:120]
        all_ok &= ok
        table.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(table)
    return all_ok
